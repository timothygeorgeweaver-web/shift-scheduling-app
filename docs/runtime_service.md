# Local scheduling service

The user service runs the application from:
`~/shift-scheduling-app`, using its existing virtual environment and
application configuration.

Browser: http://127.0.0.1:8010

The service listens on loopback only. It restarts after process failure
and is enabled for the user's systemd session. It does not keep a sleeping
Chromebook or stopped Linux environment awake.

Commands:

    systemctl --user status shift-scheduling.service
    systemctl --user stop shift-scheduling.service
    systemctl --user start shift-scheduling.service
    systemctl --user restart shift-scheduling.service

Stop this service before disposable database cloning and recovery operations.
Start it again after those operations finish.

Do not run the manual server while this service owns port 8010.
Restart the service after code updates so Python changes are loaded.
Browser asset changes may require a hard refresh.

The application reads its existing .env configuration directly.
Credentials are not embedded in the unit. Access logging is disabled.

The initial certification verified database readiness, terminated the main
process, and verified that systemd started a new ready process.

This local service does not establish hosted deployment, HTTPS, unattended
encrypted backups, or a live pilot.
