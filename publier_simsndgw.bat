@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  ============================================================
echo    Publication sur gmpdlorawan/simsndgw
echo    1 commit propre — SANS Cursor Agent (lancer HORS Cursor)
echo  ============================================================
echo.
echo  Ouvrez ce fichier avec un double-clic depuis l Explorateur Windows,
echo  PAS depuis le terminal integre de Cursor.
echo.
pause

git checkout --orphan main_propre 2>nul
if errorlevel 1 (
  echo ERREUR checkout. Verifiez Git.
  pause
  exit /b 1
)

git add -A
git commit --no-verify -F commit_msg.txt
if errorlevel 1 (
  echo ERREUR commit.
  pause
  exit /b 1
)

git branch -D main 2>nul
git branch -m main

echo.
echo  --- Verification (pas de Co-authored-by Cursor) ---
git log -1 --format=full
echo.

git remote remove origin 2>nul
git remote add origin https://github.com/gmpdlorawan/simsndgw.git

echo  Push vers https://github.com/gmpdlorawan/simsndgw.git
echo  Identifiant GitHub + TOKEN (pas le mot de passe).
echo.
pause

git push -u --force origin main
if errorlevel 1 (
  echo.
  echo  Echec push — verifiez token et droits sur simsndgw.
  pause
  exit /b 1
)

echo.
echo  OK — https://github.com/gmpdlorawan/simsndgw
echo  Contributors : uniquement vous (apres refresh GitHub).
echo.
pause
