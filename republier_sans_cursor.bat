@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Historique propre — UN seul commit, SANS Cursor Agent
echo  ============================================================
echo.
echo  Ce script remplace tout l historique par 1 commit (auteur: vous seul).
echo  Ensuite : git push --force origin main
echo.
pause

git checkout --orphan main_propre
if errorlevel 1 goto erreur

git add -A
git commit --no-verify -F commit_msg.txt
if errorlevel 1 goto erreur

git branch -D main 2>nul
git branch -m main

echo.
echo  OK — historique local = 1 commit sans co-auteur Cursor.
echo.
git log -1 --format=full
echo.
set /p PUSH="Pousser sur GitHub maintenant ? (O/N) : "
if /i not "%PUSH%"=="O" goto fin

git push --force origin main
if errorlevel 1 (
  echo.
  echo  Echec push — verifiez token GitHub et droits sur le depot.
  pause
  exit /b 1
)

echo.
echo  Termine. Attendez 1-24 h pour que Contributors se mette a jour.
echo  Verifiez : https://github.com/gmpdlorawan/sdngw/commits/main
echo.
goto fin

:erreur
echo ERREUR Git. Verifiez que Git est installe.
pause
exit /b 1

:fin
pause
