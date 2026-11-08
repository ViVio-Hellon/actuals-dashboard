//! 倉入実績集計 デスクトップ版の外枠(python-web-tools の外枠と同じ作り)
//!
//! 【役割の分け方】(各言語が得意なことをする)
//!
//! - **Rust(ここ)**: 窓・Python の起動と見張り・二重起動の防止・終了の確認・
//!   フォルダ/ファイルを選ぶ窓(Windows 標準)・exe を閉じたら子(SymNavi・Excel)も止める(ジョブ)・起動の記録
//! - **Python(`bridge.py` 以下)**: 判定・計算・集計・取り込み・SymNavi・ログ(ブラウザ版と同じもの)
//! - **JS(`actuals/web/static/js`)**: 画面の操作(デスクトップ版のときだけ「選ぶ…」を外枠に頼む)
//!
//! 【ポートを使わない】
//! 画面(WebView)は独自の宛先 `app://localhost/`(Windows では `http://app.localhost/`)を読む。
//! その要求をここで受けて Python の標準入力へ渡す(`bridge.rs`)。127.0.0.1 で待ち受けないので、
//! ポートの取り合い・プロキシ・セキュリティ製品に左右されない。
//!
//! 【ブラウザ版と同時に動かさない】
//! Python(`bridge.py`)がブラウザ版と同じ `web.lock` を取り合う。ブラウザ版が動いていれば
//! Python は起動せず理由を知らせ、ここが窓にその理由を出す(ブラウザ版には触らない)。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod bridge;
mod pages;

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use tauri::http::{Request, Response};
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind, MessageDialogResult};

use bridge::{Bridge, Phase};

/// 画面の宛先の名前。Python 側の `server.DESKTOP_HOSTS` と揃える
const SCHEME: &str = "app";
const TITLE: &str = "倉入実績集計";
/// ログの決まり(all-tools と同じ): ツール ID のフォルダに、ツール ID で始まり PC 名の入った名前で書く
const TOOL_ID: &str = "actualsdashboard";

fn origin() -> String {
    if cfg!(windows) {
        format!("http://{SCHEME}.localhost")
    } else {
        format!("{SCHEME}://localhost")
    }
}

fn app_url(path: &str) -> tauri::Url {
    format!("{}{}", origin(), path).parse().expect("app url")
}

fn is_app_url(url: &tauri::Url) -> bool {
    url.as_str().starts_with(&origin())
}

/// ツール一式のフォルダ(`bridge.py` がある所)。exe はその直下に置く。開発中は上へたどる。
fn app_root() -> PathBuf {
    if let Ok(root) = std::env::var("ACTUALS_ROOT") {
        if !root.trim().is_empty() {
            return PathBuf::from(root);
        }
    }
    let exe = std::env::current_exe().unwrap_or_default();
    let mut dir = exe.parent().map(Path::to_path_buf).unwrap_or_default();
    let start = dir.clone();
    for _ in 0..5 {
        if dir.join("bridge.py").is_file() {
            return dir;
        }
        match dir.parent() {
            Some(parent) => dir = parent.to_path_buf(),
            None => break,
        }
    }
    start
}

/// この PC のデータの場所(Python の `Paths.default()` と同じ決め方)
fn data_root() -> PathBuf {
    if let Ok(dir) = std::env::var("ACTUALS_DASHBOARD_HOME") {
        if !dir.trim().is_empty() {
            return PathBuf::from(dir.trim());
        }
    }
    let home = std::env::var("USERPROFILE").or_else(|_| std::env::var("HOME")).unwrap_or_default();
    PathBuf::from(home).join("ActualsDashboard")
}

fn log_hint() -> String {
    data_root().join("logs").join(TOOL_ID).display().to_string()
}

/// ファイル名に入れる PC 名(使えない字と空白は `_`。Python の `logsetup.safe` と同じ)
fn pc_name() -> String {
    let raw = std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")).unwrap_or_default();
    let raw = if raw.trim().is_empty() {
        std::fs::read_to_string("/etc/hostname").unwrap_or_default()
    } else {
        raw
    };
    let out: String = raw
        .trim()
        .chars()
        .map(|c| if "\\/:*?\"<>|".contains(c) || c.is_whitespace() { '_' } else { c })
        .collect();
    let out = out.trim_matches('_').to_string();
    if out.is_empty() { "PC".into() } else { out }
}

/// 起動の記録: `<この PC の logs>\actualsdashboard\actualsdashboard_起動_<PC>_<YYYYMMDD>.log`。
/// Python が動く前・動けない時のこと(どの Python を使ったか・起動できない理由・終了)はここにしか残らない。
/// 書けなくても止めない。
fn launch_log(line: &str) {
    let now = chrono::Local::now();
    let dir = data_root().join("logs").join(TOOL_ID);
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join(format!("{TOOL_ID}_起動_{}_{}.log", pc_name(), now.format("%Y%m%d")));
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} [exe {}] {}", now.format("%Y-%m-%d %H:%M:%S%.3f"), std::process::id(), line);
    }
}

/// **2つ目を起動させない**ための錠(OS のファイルロック)。落ちても OS が外すので残らない。
fn take_instance_lock() -> Option<std::fs::File> {
    let dir = data_root().join("runtime");
    let _ = std::fs::create_dir_all(&dir);
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join("desktop.lock"))
        .ok()?;
    match file.try_lock() {
        Ok(()) => Some(file),
        Err(_) => None,
    }
}

fn html(status: u16, body: String) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header("Content-Type", "text/html; charset=utf-8")
        .header("Cache-Control", "no-store")
        .body(body.into_bytes())
        .unwrap()
}

fn json_error(status: u16, code: &str, message: &str) -> Response<Vec<u8>> {
    let body = serde_json::json!({"error": {"code": code, "message": message}});
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .body(serde_json::to_vec(&body).unwrap())
        .unwrap()
}

/// 画面からの要求1件を Python へ渡す。
fn handle(bridge: &Bridge, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let uri = request.uri().clone();
    let path = uri.path().to_string();
    let wants_page = request.method() == "GET" && !path.starts_with("/api/") && !path.contains('.');

    if bridge.phase() == Phase::Starting {
        let limit = if wants_page { Duration::from_millis(400) } else { Duration::from_secs(120) };
        if !bridge.wait_started(limit) && bridge.phase() == Phase::Starting {
            return html(200, pages::starting());
        }
    }
    let down = |reason: &str| {
        if wants_page || path == "/" {
            html(503, pages::failure(bridge.failure().as_ref(), &bridge.python(), &bridge.stderr_tail(), &log_hint()))
        } else {
            json_error(503, "python_down", reason)
        }
    };
    if bridge.phase() != Phase::Started {
        return down("Python の処理が動いていません。アプリを開き直してください。");
    }

    let host = uri.host().unwrap_or("app.localhost").to_string();
    let mut headers: Vec<(String, String)> = request
        .headers()
        .iter()
        .filter_map(|(k, v)| Some((k.as_str().to_string(), v.to_str().ok()?.to_string())))
        .filter(|(k, _)| !k.eq_ignore_ascii_case("host"))
        .collect();
    headers.push(("Host".into(), host));

    if request.body().len() > bridge::MAX_BODY {
        return json_error(413, "too_large", bridge::TOO_LARGE_MESSAGE);
    }

    match bridge.call(request.method().as_str(), &path, uri.query().unwrap_or(""), headers, request.body()) {
        Ok(reply) if wants_page && (300..400).contains(&reply.status) => {
            let target = reply
                .headers
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case("location"))
                .map(|(_, v)| v.clone())
                .unwrap_or_else(|| "/".into());
            html(200, pages::moving_to(&target))
        }
        Ok(reply) => {
            let mut builder = Response::builder().status(reply.status);
            for (k, v) in &reply.headers {
                if k.eq_ignore_ascii_case("content-length") || k.eq_ignore_ascii_case("transfer-encoding") {
                    continue;
                }
                builder = builder.header(k.as_str(), v.as_str());
            }
            builder.body(reply.body).unwrap_or_else(|_| json_error(500, "bad_reply", "応答を組み立てられませんでした"))
        }
        Err(reason) => down(&reason),
    }
}

// ------------------------------------------------------------------
// 画面から呼ぶもの(`actuals/web/static/js/picker.js`)
// ------------------------------------------------------------------
/// 「選ぶ…」: Windows 標準のフォルダ・ファイルを選ぶ窓。選んだフルパス(やめたら None)。
/// `ext` は ".rne" や ".exe" のような拡張子(ファイルの時の絞り込み)。
#[tauri::command]
async fn pick_path(app: AppHandle, kind: String, ext: String, start: String, title: String) -> Result<Option<String>, String> {
    let mut dialog = app.dialog().file();
    if !title.is_empty() {
        dialog = dialog.set_title(title);
    }
    let start_dir = {
        let p = PathBuf::from(start.trim());
        if p.is_dir() { Some(p) } else { p.parent().filter(|d| d.is_dir()).map(Path::to_path_buf) }
    };
    if let Some(dir) = start_dir {
        dialog = dialog.set_directory(dir);
    }
    let picked = if kind == "dir" {
        dialog.blocking_pick_folder()
    } else {
        let exts: Vec<String> = ext
            .split(',')
            .map(|e| e.trim().trim_start_matches('.').to_string())
            .filter(|e| !e.is_empty())
            .collect();
        if !exts.is_empty() {
            let refs: Vec<&str> = exts.iter().map(String::as_str).collect();
            dialog = dialog.add_filter(exts.join(", "), &refs);
        }
        dialog.blocking_pick_file()
    };
    Ok(picked.and_then(|p| p.into_path().ok()).map(|p| p.display().to_string()))
}

// ------------------------------------------------------------------
// 終わり方
// ------------------------------------------------------------------
static CONFIRMING: AtomicBool = AtomicBool::new(false);
const QUIT_YES: &str = "終了する";
const QUIT_NO: &str = "やめる";

/// 窓の × / stop.bat の「閉じて」。**画面の「終了」と同じ確認を通る**(データ更新の実行中なら訊く)。
fn confirm_close(app: AppHandle, bridge: Arc<Bridge>) {
    if CONFIRMING.swap(true, Ordering::SeqCst) {
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
        return;
    }
    thread::spawn(move || {
        let json = vec![("Content-Type".to_string(), "application/json".to_string())];
        match bridge.call("POST", "/api/shutdown", "", json.clone(), b"{}") {
            // データ更新の実行中などで断られた(422 / 409)。理由を見せて訊く
            Ok(reply) if reply.status == 422 || reply.status == 409 => {
                let message = refusal_message(&reply.body);
                let answer = app
                    .dialog()
                    .message(format!("{message}\n\n中断して終了しますか?"))
                    .title(TITLE)
                    .kind(MessageDialogKind::Warning)
                    .buttons(MessageDialogButtons::OkCancelCustom(QUIT_YES.into(), QUIT_NO.into()))
                    .blocking_show_with_result();
                if chose_quit(&answer) {
                    let _ = bridge.call("POST", "/api/shutdown", "", json, br#"{"force": true}"#);
                    exit_soon(app);
                } else {
                    CONFIRMING.store(false, Ordering::SeqCst);
                }
            }
            _ => exit_soon(app),
        }
    });
}

/// 断りの文(`{"error": {"message": …}}`)。読めなければ決まった文
fn refusal_message(body: &[u8]) -> String {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|v| {
            v.pointer("/error/message")
                .or_else(|| v.get("message"))
                .and_then(|m| m.as_str())
                .map(str::to_string)
        })
        .unwrap_or_else(|| "実行中の処理があります。".into())
}

/// 「終了する」をはっきり押した時だけ真(窓が出せなかった・× で閉じた・知らない答えは「やめる」)
fn chose_quit(answer: &MessageDialogResult) -> bool {
    match answer {
        MessageDialogResult::Custom(label) => label == QUIT_YES,
        MessageDialogResult::Ok => true,
        _ => false,
    }
}

fn exit_soon(app: AppHandle) {
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(1500));
        app.exit(0);
    });
}

fn main() {
    let root = app_root();
    let bridge = Bridge::new(root.clone(), bridge::new_token());
    launch_log(&format!("起動: {TITLE} デスクトップ版 {} ツールのフォルダ {}", env!("CARGO_PKG_VERSION"), root.display()));

    let for_protocol = bridge.clone();
    let for_close = bridge.clone();
    let for_setup = bridge.clone();
    let for_exit = bridge.clone();

    let app = tauri::Builder::default()
        // 2つ目を開こうとしたら、開いている窓を前に出すだけ
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .register_asynchronous_uri_scheme_protocol(SCHEME, move |_ctx, request, responder| {
            let bridge = for_protocol.clone();
            thread::spawn(move || responder.respond(handle(&bridge, request)));
        })
        .invoke_handler(tauri::generate_handler![pick_path])
        .on_window_event(move |window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    confirm_close(window.app_handle().clone(), for_close.clone());
                }
            }
        })
        .setup(move |app| {
            match take_instance_lock() {
                Some(lock) => {
                    Box::leak(Box::new(lock));
                }
                None => {
                    launch_log("起動しません: デスクトップ版がもう動いています(その窓を前に出します)");
                    app.handle().exit(0);
                    return Ok(());
                }
            }
            let handle = app.handle().clone();
            for_setup.set_on_quit(move || handle.exit(0));
            let handle = app.handle().clone();
            for_setup.set_on_lost(move || {
                launch_log("Python が途中で止まりました(理由の画面を出します)");
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.eval("location.reload()");
                }
            });
            let starter = for_setup.clone();
            thread::spawn(move || {
                starter.start();
                match starter.failure() {
                    Some(f) => launch_log(&format!("起動できません: {} / {} (Python: {})", f.message, f.hint, starter.python())),
                    None => launch_log(&format!("Python を起動しました: {}", starter.python())),
                }
            });

            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(app_url("/")))
                .title(TITLE)
                .inner_size(1500.0, 950.0)
                .min_inner_size(1200.0, 700.0)
                .center()
                .on_navigation(|url| is_app_url(url))
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("アプリを組み立てられませんでした");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // 標準入力を閉じて Python に終わってもらう。終わらなければ止める(ジョブで子も止まる)
            for_exit.shutdown(Duration::from_secs(8));
            launch_log("終了しました");
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 画面の宛先はosで決まる() {
        let url = app_url("/api/month?y=2022");
        assert!(is_app_url(&url));
        assert!(url.as_str().ends_with("/api/month?y=2022"));
        assert!(!is_app_url(&"http://127.0.0.1:8751/".parse().unwrap()));
    }

    #[test]
    fn 終了の確かめは終了するを押したときだけ終わる() {
        assert!(chose_quit(&MessageDialogResult::Custom(QUIT_YES.into())));
        assert!(chose_quit(&MessageDialogResult::Ok));
        assert!(!chose_quit(&MessageDialogResult::Custom(QUIT_NO.into())));
        assert!(!chose_quit(&MessageDialogResult::Cancel));
        assert!(!chose_quit(&MessageDialogResult::Custom("".into())));
    }

    #[test]
    fn 断りの文を読む() {
        assert_eq!(refusal_message(r#"{"error": {"code": "rejected", "message": "データ更新の実行中です。"}}"#.as_bytes()),
                   "データ更新の実行中です。");
        assert_eq!(refusal_message(b"broken"), "実行中の処理があります。");
    }

    #[test]
    fn pc名はファイル名に使える字だけ() {
        std::env::set_var("COMPUTERNAME", "NLM NGY:240638");
        assert_eq!(pc_name(), "NLM_NGY_240638");
        std::env::remove_var("COMPUTERNAME");
    }

    #[test]
    fn データの場所と錠と起動の記録() {
        let dir = std::env::temp_dir().join(format!("ad_desktop_test_{}", std::process::id()));
        std::env::set_var("ACTUALS_DASHBOARD_HOME", &dir);
        assert_eq!(data_root(), dir);
        let first = take_instance_lock();
        assert!(first.is_some(), "1つ目は錠を取れる");
        assert!(take_instance_lock().is_none(), "2つ目は取れない");
        drop(first);
        assert!(take_instance_lock().is_some(), "1つ目が終われば取れる");
        launch_log("試験");
        let logs: Vec<String> = std::fs::read_dir(dir.join("logs").join(TOOL_ID))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
            .collect();
        assert!(logs.iter().any(|n| n.starts_with("actualsdashboard_起動_") && n.ends_with(".log")), "{logs:?}");
        std::env::remove_var("ACTUALS_DASHBOARD_HOME");
    }
}
