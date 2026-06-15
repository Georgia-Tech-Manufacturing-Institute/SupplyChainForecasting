@echo off
echo Starting Forecast Tool...
echo Hit Ctrl + C to stop/terminate the container and UI
echo If it doesn't happen automatically, open a browser and go to localhost:5000
docker compose up
echo.
echo Ready! Opening browser...
start http://localhost:5000