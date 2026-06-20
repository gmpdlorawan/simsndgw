@echo off
REM ============================================================================
REM SDN-GW — Script de lancement (double-clic ou ligne de commande)
REM   Sans argument  : interface web Streamlit (simulation interactive)
REM   Avec "eval"    : simulation batch uniquement (CSV + graphiques PNG)
REM ============================================================================
chcp 65001 >nul
cd /d "%~dp0"

title SDN-GW — Lancement

echo.
echo  [SDN-GW] Repertoire : %CD%
echo.

REM --- Choix de l'interpreteur Python (Windows : python ou py -3) ---
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY (
  where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo  ERREUR : Python introuvable.
  echo  Installez Python 3 depuis python.org et cochez "Add Python to PATH".
  pause
  exit /b 1
)

echo  [SDN-GW] Python : %PY%
%PY% --version
echo.

echo  [SDN-GW] Installation des dependances ^(requirements.txt^)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo  ERREUR : pip install a echoue.
  pause
  exit /b 1
)

REM --- Mode evaluation batch : lancer.bat eval ---
if /i "%~1"=="eval" (
  echo.
  echo  [SDN-GW] Execution : eval_sdn_gw.py ^(resultats CSV + dossier plots^)
  echo.
  %PY% eval_sdn_gw.py --output sdn_gw_eval_results.csv
  echo.
  echo  Termine : sdn_gw_eval_results.csv et plots\comparison_*.png
  pause
  exit /b 0
)

REM --- Mode par defaut : interface Streamlit ---
echo.
echo  ============================================================
echo    INTERFACE — Ouvrez dans le navigateur ^(apres demarrage^) :
echo.
echo       http://127.0.0.1:8501
echo.
echo    Puis dans la barre laterale : clic sur "Lancer / recalculer"
echo    Ne fermez pas cette fenetre tant que vous utilisez l interface.
echo  ============================================================
echo.
echo    Simulation CSV sans interface : lancer.bat eval
echo.

REM Ouverture navigateur apres ~5 s ^(le temps que Streamlit demarre^)
start /min cmd /c "ping -n 6 127.0.0.1 >nul && start http://127.0.0.1:8501/"

set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
%PY% -m streamlit run app_simulation.py --server.address 127.0.0.1 --server.port 8501

echo.
pause
