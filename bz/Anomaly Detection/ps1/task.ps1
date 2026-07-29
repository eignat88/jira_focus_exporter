$n = Get-ChildItem .\ -Filter "task*.md" |
  Where-Object { $_.BaseName -match '^task(\d+)$' } |
  ForEach-Object { [int]$Matches[1] } |
  Sort-Object -Descending |
  Select-Object -First 1

$next = if ($n) { $n + 1 } else { 1 }
$file = ".\task$next.md"

@"
# Task $next

"@ | Set-Content -Encoding UTF8 $file

code $file
