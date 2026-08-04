' Wrapper que roda o refresh.bat SEM abrir janela.
' Windows Task Scheduler chama esse .vbs; ele dispara o .bat com hide=0 (invisível) e não espera.
Set sh = CreateObject("Wscript.Shell")
sh.Run "cmd /c """ & "C:\Users\compu\code\painel-sapron-pipefy\refresh.bat" & """", 0, False
