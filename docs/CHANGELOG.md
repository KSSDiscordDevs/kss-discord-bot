4/24/2026 Upgrade to a persistent database connection to reduce database latency from close to 1s down to 80 ms.

9/9/2026 Add a persistent "Interest Channels" dropdown (`/spawn_interest_menu`) so members can join or leave opt-in channels like #ice-skating themselves instead of being added by hand. Channels are configured in `INTEREST_CHANNELS` in `bot/config.py`.

9/9/2026 Fix the interest menu: picking a channel now shows explicit Join / Leave buttons instead of silently toggling access (which removed members who had been added by hand), and the dropdown resets after each use so the same channel can be selected again.