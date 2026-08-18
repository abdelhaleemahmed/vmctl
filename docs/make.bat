@ECHO OFF
REM vmctl documentation build for Windows (cmd / PowerShell).
REM Usage:  make.bat html | clean

setlocal
set SPHINXBUILD=python -m sphinx
set SOURCEDIR=source
set BUILDDIR=build

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="clean" goto clean
if "%1"=="html" goto html
goto help

:help
echo html    Build HTML  -^> %BUILDDIR%\html
echo clean   Remove the build directory
goto end

:clean
if exist %BUILDDIR% rmdir /S /Q %BUILDDIR%
goto end

:html
%SPHINXBUILD% -b html %SOURCEDIR% %BUILDDIR%\html
goto end

:end
endlocal
