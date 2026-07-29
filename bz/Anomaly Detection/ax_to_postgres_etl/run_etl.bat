@echo off
REM Запуск ETL от имени ALKOR\ignatchenko-adm
REM Использовать Trusted_Connection=yes (Windows Authentication)

echo ============================================
echo ETL: SQL Server AX2012 → PostgreSQL
echo ============================================
echo.

REM Запуск Python скрипта
python main.py

echo.
echo ============================================
echo Готово!
echo ============================================
pause
