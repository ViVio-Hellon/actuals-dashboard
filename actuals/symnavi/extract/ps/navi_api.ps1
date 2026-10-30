# Navigator Extended API (SymNaviA.dll) probe. Runs in 32-bit PowerShell (SymNaviA.dll is 32-bit).
# Called by app/extract/naviapi.py. Inputs come from environment variables (the password is never on the command line):
#   RNE_NAVI_DLL     full path of SymNaviA.dll (usually C:\NAVICL\Rtl\SymNaviA.dll)
#   RNE_NAVI_BIN     folder with the Navigator DLLs (usually C:\NAVICL\bin)
#   RNE_NAVI_ID / RNE_NAVI_PASS / RNE_NAVI_SERVER   login
#   RNE_NAVI_JOBS    JSON file: [{"tag": "...", "rne": "C:\...\x.RNE", "out": "C:\...\x.api.txt"}, ...]
#   RNE_NAVI_RESULT  JSON file to write the result to
# Every step prints one line "step name=... tag=... sec=... rc=... err=..." so Python can show progress and keep a record.
# Records are written to "out" as UTF-8, one record per line (CR/LF inside a record are written as \r \n).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # keep CLIXML progress records off stdout
$result = [ordered]@{ ok = $false; connect = ""; ps_version = "$($PSVersionTable.PSVersion)"; ptr_size = [IntPtr]::Size;
                      language_mode = "$($ExecutionContext.SessionState.LanguageMode)"; steps = @(); jobs = [ordered]@{}; error = "" }
function Save-Result { $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $env:RNE_NAVI_RESULT -Encoding UTF8 }
function Say([string]$line) { [Console]::Out.WriteLine($line); [Console]::Out.Flush() }

try {
  if ([IntPtr]::Size -ne 4) { throw "not 32-bit PowerShell (IntPtr.Size=$([IntPtr]::Size))" }
  $src = @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class NaviApi {
  [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)] public static extern bool SetDllDirectory(string p);
  [DllImport("kernel32", SetLastError = true, CharSet = CharSet.Unicode)] public static extern IntPtr LoadLibrary(string p);
  [DllImport("oleaut32")] public static extern int SysStringByteLen(IntPtr b);
  [DllImport("oleaut32")] public static extern void SysFreeString(IntPtr b);
  [DllImport("SymNaviA.dll", CharSet = CharSet.Ansi)] public static extern void NaviOpenSession(out int rc, string user, string pass, string server);
  [DllImport("SymNaviA.dll")] public static extern void NaviCloseSession();
  [DllImport("SymNaviA.dll")] public static extern void NaviConnectResourceNoAuth(out int rc);
  [DllImport("SymNaviA.dll", CharSet = CharSet.Ansi)] public static extern void NaviConnectOracle(out int rc, string user, string pass);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetControlPointNumber(IntPtr doc, out int rc, int kind, out int n);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetDataItemNumber(IntPtr doc, out int rc, int kind, out int n);
  [DllImport("SymNaviA.dll")] public static extern IntPtr NaviGetControlPoint2(IntPtr doc, out int rc, int a, int b);
  [DllImport("SymNaviA.dll")] public static extern IntPtr NaviGetDataItem2(IntPtr doc, out int rc, int a, int b);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetNameCP(IntPtr cp, out int rc, int which, ref IntPtr text);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetNameDI(IntPtr di, out int rc, ref IntPtr text);
  public static string NameCP(IntPtr cp, int which, out int rc) { IntPtr b = IntPtr.Zero; NaviGetNameCP(cp, out rc, which, ref b); return TakeBstr(ref b); }
  public static string NameDI(IntPtr di, out int rc) { IntPtr b = IntPtr.Zero; NaviGetNameDI(di, out rc, ref b); return TakeBstr(ref b); }
  [DllImport("SymNaviA.dll", CharSet = CharSet.Ansi)] public static extern IntPtr NaviOpenCatalog(out int rc, string path);
  [DllImport("SymNaviA.dll")] public static extern void NaviCloseCatalog(IntPtr doc);
  [DllImport("SymNaviA.dll")] public static extern void NaviExecuteCatalog(IntPtr doc, out int rc, out int state, int mode, int unused);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetRecordNumber(IntPtr doc, out int rc, out int n);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetFieldNumber(IntPtr doc, out int rc, out int n);
  [DllImport("SymNaviA.dll")] public static extern void NaviDownLoadData(IntPtr doc, out int rc, int count, out int got);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetRecordData(IntPtr doc, out int rc, int row, int unused, ref IntPtr text);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetErrorCode(out int code);
  [DllImport("SymNaviA.dll")] public static extern void NaviGetErrorMessage(out int rc, ref IntPtr text);
  static readonly Encoding Sjis = Encoding.GetEncoding(932);
  // SymNaviA returns text as a BSTR that holds ANSI (Shift-JIS) bytes (SysAllocStringByteLen)
  public static string TakeBstr(ref IntPtr b) {
    if (b == IntPtr.Zero) return null;
    int n = SysStringByteLen(b);
    byte[] buf = new byte[n];
    Marshal.Copy(b, buf, 0, n);
    SysFreeString(b);
    b = IntPtr.Zero;
    return Sjis.GetString(buf);
  }
  public static string Record(IntPtr doc, int row, out int rc) {
    IntPtr b = IntPtr.Zero;
    NaviGetRecordData(doc, out rc, row, 0, ref b);
    return TakeBstr(ref b);
  }
  public static string LastError() {
    int code; NaviGetErrorCode(out code);
    int rc; IntPtr b = IntPtr.Zero;
    NaviGetErrorMessage(out rc, ref b);
    string m = TakeBstr(ref b);
    return code.ToString() + ":" + (m ?? "");
  }
}
'@
  Add-Type -TypeDefinition $src -Language CSharp
  [void][NaviApi]::SetDllDirectory($env:RNE_NAVI_BIN)
  Set-Location -LiteralPath $env:RNE_NAVI_BIN
  $h = [NaviApi]::LoadLibrary($env:RNE_NAVI_DLL)
  if ($h -eq [IntPtr]::Zero) { throw "LoadLibrary failed: $($env:RNE_NAVI_DLL) (error $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" }

  $sw = [Diagnostics.Stopwatch]::StartNew()
  $script:lastErr = ""
  function Step([string]$name, [string]$tag, [int]$rc, [string]$extra = "") {
    $sec = [math]::Round($sw.Elapsed.TotalSeconds, 2)
    $err = if ($rc -ne 0) { [NaviApi]::LastError() } else { "" }
    if ($err) { $script:lastErr = $err }
    $s = [ordered]@{ name = $name; tag = $tag; sec = $sec; rc = $rc; err = $err; extra = $extra }
    $script:result.steps += $s
    Say ("step name=$name tag=$tag sec=$sec rc=$rc" + $(if ($extra) { " $extra" } else { "" }) + $(if ($err) { " err=$err" } else { "" }))
    $sw.Restart()
  }

  $rc = 0
  [NaviApi]::NaviOpenSession([ref]$rc, $env:RNE_NAVI_ID, $env:RNE_NAVI_PASS, $env:RNE_NAVI_SERVER)
  Step "login" "" $rc
  if ($rc -ne 0) { throw "login failed" }
  # SymNavi.exe connects the database resource right after logon (ConnectResourceNoAuth = the connection info
  # registered on the server). If an RNE still cannot be opened because of the database, it falls back to
  # ConnectOracle(user, password). Do the same, and record which one worked.
  $rcA = 0
  [NaviApi]::NaviConnectResourceNoAuth([ref]$rcA)
  Step "connect_resource_noauth" "" $rcA
  $result.connect = "noauth rc=$rcA"
  # The 1st real run: NoAuth alone was not enough (KVR70604) and ConnectOracle(ID, password) worked. Connect up front
  $rcB = 0
  [NaviApi]::NaviConnectOracle([ref]$rcB, $env:RNE_NAVI_ID, $env:RNE_NAVI_PASS)
  Step "connect_oracle" "" $rcB
  $result.connect += " / oracle rc=$rcB"
  $oracleTried = ($rcB -eq 0)

  $jobs = Get-Content -LiteralPath $env:RNE_NAVI_JOBS -Raw -Encoding UTF8 | ConvertFrom-Json
  foreach ($j in $jobs) {
    $tag = [string]$j.tag
    $job = [ordered]@{ ok = $false; sec = [ordered]@{}; records = -1; fields = -1; rows_written = 0; first_row = -1;
                       sample = @(); tabs_in_first = -1; error = ""; download = @() }
    $result.jobs[$tag] = $job
    $doc = [IntPtr]::Zero
    $t0 = [Diagnostics.Stopwatch]::StartNew()
    try {
      $doc = [NaviApi]::NaviOpenCatalog([ref]$rc, [string]$j.rne)
      $job.sec.open = [math]::Round($sw.Elapsed.TotalSeconds, 2); Step "open" $tag $rc
      if (($rc -ne 0 -or $doc -eq [IntPtr]::Zero) -and -not $oracleTried) {
        # Database not connected / rejected: connect Oracle with the Navigator login once, then open again
        $oracleTried = $true
        if ($doc -ne [IntPtr]::Zero) { [NaviApi]::NaviCloseCatalog($doc); $doc = [IntPtr]::Zero }
        $rcB = 0
        [NaviApi]::NaviConnectOracle([ref]$rcB, $env:RNE_NAVI_ID, $env:RNE_NAVI_PASS)
        Step "connect_oracle" $tag $rcB
        $result.connect += " / oracle rc=$rcB"
        $doc = [NaviApi]::NaviOpenCatalog([ref]$rc, [string]$j.rne)
        $job.sec.open = [math]::Round($sw.Elapsed.TotalSeconds, 2); Step "open" $tag $rc "retry=1"
      }
      if ($rc -ne 0 -or $doc -eq [IntPtr]::Zero) { throw "open failed" }

      $state = 0
      [NaviApi]::NaviExecuteCatalog($doc, [ref]$rc, [ref]$state, 1, 0)
      if ($rc -ne 0) {
        # Some resources need an explicit connect. Try once, then execute again
        $e1 = [NaviApi]::LastError(); Step "execute" $tag $rc "state=$state"
        $rc2 = 0; [NaviApi]::NaviConnectResourceNoAuth([ref]$rc2); Step "connect_noauth" $tag $rc2
        [NaviApi]::NaviExecuteCatalog($doc, [ref]$rc, [ref]$state, 1, 0)
      }
      $job.sec.execute = [math]::Round($sw.Elapsed.TotalSeconds, 2); Step "execute" $tag $rc "state=$state"
      if ($rc -ne 0) { throw "execute failed" }

      $n = 0; [NaviApi]::NaviGetRecordNumber($doc, [ref]$rc, [ref]$n); $before = $n
      $f = 0; [NaviApi]::NaviGetFieldNumber($doc, [ref]$rc, [ref]$f); $job.fields = $f
      Step "count_after_execute" $tag $rc "records=$n fields=$f"

      # Bring the rest of the records to the client. SymNavi.exe calls DownLoadData(65535 - already loaded)
      $dl = 0.0
      $rc2 = 0
      for ($k = 0; $k -lt 20; $k++) {
        $got = 0
        $ask = [Math]::Max(1, 65535 - $n)
        [NaviApi]::NaviDownLoadData($doc, [ref]$rc, $ask, [ref]$got)
        $dl += $sw.Elapsed.TotalSeconds
        [NaviApi]::NaviGetRecordNumber($doc, [ref]$rc2, [ref]$n)
        $job.download += [ordered]@{ rc = $rc; got = $got; records = $n }
        Step "download" $tag $rc "got=$got records=$n"
        if ($rc -ne 0 -or $got -le 0 -or $got -lt $ask -or $n -ge 65535) { break }
      }
      $job.sec.download = [math]::Round($dl, 2)
      $job.records = $n

      # Which index is the first record (0 or 1)
      $r0 = [NaviApi]::Record($doc, 0, [ref]$rc); $rc0 = $rc
      $first = if ($rc0 -eq 0 -and $r0 -ne $null) { 0 } else { 1 }
      $job.first_row = $first
      $w = New-Object IO.StreamWriter([string]$j.out, $false, (New-Object Text.UTF8Encoding($false)))
      try {
        for ($i = $first; $i -lt $first + $n; $i++) {
          $line = [NaviApi]::Record($doc, $i, [ref]$rc)
          if ($rc -ne 0) { $job.error = "record $i rc=$rc " + [NaviApi]::LastError(); break }
          if ($line -eq $null) { $line = "" }
          if ($job.rows_written -lt 3) {
            $job.sample += ($line -replace "`t", "<TAB>" -replace "`r", "<CR>" -replace "`n", "<LF>")
            if ($job.rows_written -eq 0) { $job.tabs_in_first = ($line.ToCharArray() | Where-Object { $_ -eq "`t" }).Count }
          }
          $w.WriteLine(($line -replace "`r", "\r" -replace "`n", "\n"))
          $job.rows_written++
          if ($job.rows_written % 5000 -eq 0) { Say "progress tag=$tag rows=$($job.rows_written) of=$n" }
        }
      } finally { $w.Close() }
      $job.sec.read = [math]::Round($sw.Elapsed.TotalSeconds, 2); Step "read" $tag 0 "rows=$($job.rows_written) first=$first"
      $job.ok = ($job.error -eq "")
      Save-Result   # keep what we have, in case the layout probe below fails hard

      # Column names (records have none). The meaning of the first argument (layout position) is not documented,
      # so try 0..7 and record what each returns
      $layout = @()
      $cols = @()
      for ($kind = 0; $kind -le 7; $kind++) {
        $nc = 0; [NaviApi]::NaviGetControlPointNumber($doc, [ref]$rc, $kind, [ref]$nc)
        if ($rc -eq 0 -and $nc -gt 0) {
          for ($i = 0; $i -lt [Math]::Min($nc, 100); $i++) {
            $cp = [NaviApi]::NaviGetControlPoint2($doc, [ref]$rc, $kind, $i)
            if ($rc -ne 0 -or $cp -eq [IntPtr]::Zero) { $layout += "cp kind=$kind i=$i rc=$rc"; break }
            $r1 = 0; $n0 = [NaviApi]::NameCP($cp, 0, [ref]$r1); $r2 = 0; $n1 = [NaviApi]::NameCP($cp, 1, [ref]$r2)
            $layout += "cp kind=$kind i=$i name0=$n0 name1=$n1"
            $cols += [ordered]@{ what = "cp"; kind = $kind; i = $i; name = $n0 }
          }
        }
        $nd = 0; [NaviApi]::NaviGetDataItemNumber($doc, [ref]$rc, $kind, [ref]$nd)
        if ($rc -eq 0 -and $nd -gt 0) {
          for ($i = 0; $i -lt [Math]::Min($nd, 100); $i++) {
            $di = [NaviApi]::NaviGetDataItem2($doc, [ref]$rc, $kind, $i)
            if ($rc -ne 0 -or $di -eq [IntPtr]::Zero) { $layout += "di kind=$kind i=$i rc=$rc"; break }
            $r1 = 0; $n0 = [NaviApi]::NameDI($di, [ref]$r1)
            $layout += "di kind=$kind i=$i name=$n0"
            $cols += [ordered]@{ what = "di"; kind = $kind; i = $i; name = $n0 }
          }
        }
      }
      $job.layout = $layout
      $job.cols = $cols
      Step "layout" $tag 0 "items=$($layout.Count)"
    } catch {
      $job.error = "$($_.Exception.Message) ($script:lastErr)"
    } finally {
      if ($doc -ne [IntPtr]::Zero) { [NaviApi]::NaviCloseCatalog($doc) }
      $job.sec.total = [math]::Round($t0.Elapsed.TotalSeconds, 2)
      Say "job tag=$tag ok=$($job.ok) total=$($job.sec.total) rows=$($job.rows_written)"
    }
  }
  [NaviApi]::NaviCloseSession()
  Step "logout" "" 0
  $result.ok = $true
} catch {
  $result.error = "$($_.Exception.Message)"
  Say "error $($result.error)"
  try { [NaviApi]::NaviCloseSession() } catch { }
} finally {
  Save-Result
}
