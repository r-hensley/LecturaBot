# LecturaBot

For an authorized reload through the bot host, use the [private control socket](README.md#reload-features-from-the-bot-host). Run `status` first and reload only after the requested feature code is on the host. The socket reloads `lecturabot.features`; queue views, the controller, and other startup code need a service restart.
