@echo off
rem Windows CLI shim: lets `kb ...` resolve to the Python entry via PATHEXT,
rem so direct command-line use works the same as `python bin\kb ...`. The
rem plugin and the test harness invoke kb through the interpreter explicitly
rem and do not depend on this shim.
python "%~dp0kb" %*
