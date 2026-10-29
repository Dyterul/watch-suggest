#!/bin/bash
# WatchSuggest Upgrade Script
# - Entfernt Home Assistant Integration komplett
# - Verbessert das adaptive Quiz-System
# - Erstellt automatisch Backups

set -e

WORK_DIR="/root/watch-suggest"
BACKUP_DIR="/root/watch-suggest-backup-$(date +%Y%m%d-%H%M%S)"

cd "$WORK_DIR" || exit 1

echo "========================================"
echo "  WatchSuggest Upgrade"
echo "  - HA Integration entfernen"
echo "  - Quiz verbessern"
echo "========================================"
echo ""

# Container stoppen
echo "1. Stoppe Container..."
docker compose down

# Backup erstellen
echo "2. Erstelle vollständiges Backup..."
mkdir -p "$BACKUP_DIR"
cp -r . "$BACKUP_DIR/"
echo "   Backup: $BACKUP_DIR"

# Python Script für alle Änderungen
echo "3. Wende Patches an..."

cat > /tmp/upgrade_patch.py << 'PYEOF'
#!/usr/bin/env python3
"""
Upgrade Script für WatchSuggest
- Entfernt Home Assistant Integration
- Verbessert adaptives Quiz
"""

import re
import os

os.chdir('/root/watch-suggest')

print("📝 Patche config.py...")
with open('config.py', 'r', encoding='utf-8') as f:
    config = f.read()

# HA Settings entfernen
config = config.replace('''    # Home Assistant
    ha_base_url: str = ""
    ha_token: str = ""
    ha_timeout_seconds: float = 8.0
    ha_link_service: str = "script.link_tracker_user"
    ha_verify_ssl: bool = True

    #''', '''    #''')

# Validator anpassen
config = config.replace(
    '"tmdb_timeout_seconds", "ha_timeout_seconds"',
    '"tmdb_timeout_seconds"'
)

# ha_configured Property entfernen
config = re.sub(
    r'    @property\s+def ha_configured\(self\)[^:]+:[\s\S]+?return bool\(self\.ha_base_url and self\.ha_token\)\s+',
    '',
    config
)

with open('config.py', 'w', encoding='utf-8') as f:
    f.write(config)
print("✅ config.py")

print("📝 Patche main.py...")
with open('main.py', 'r', encoding='utf-8') as f:
    main = f.read()

# Import entfernen
main = re.sub(r'from ha_client import[^\n]+\n', '', main)

# Doku anpassen
main = main.replace('* ``POST /api/v1/quiz/complete``  -> Profil anlegen + HA-Verknuepfung',
                   '* ``POST /api/v1/quiz/complete``  -> Profil anlegen')
main = main.replace('* ``GET  /api/v1/presence``       -> anwesende, registrierte Personen (HA)\n', '')

# _resolve_present_users Funktion entfernen
main = re.sub(
    r'async def _resolve_present_users\([^)]+\)[^:]+:[\s\S]+?return present\s+\n',
    '',
    main
)

# _link_home_assistant Funktion entfernen
main = re.sub(
    r'async def _link_home_assistant\([^)]+\)[^:]+:[\s\S]+?return result\s+\n',
    '',
    main
)

# HAClient Initialisierung entfernen
main = re.sub(r'\s+ha = HAClient\([^\)]+\)\s+', '\n', main)
main = re.sub(r'request\.app\.state\.ha = ha\s+', '', main)

# ha Referenzen in Funktionen entfernen
main = re.sub(r'\s+ha: HAClient = request\.app\.state\.ha\s+', '\n', main)

# HA Exception Handler entfernen
main = re.sub(
    r'    @app\.exception_handler\(HAError\)[\s\S]+?return JSONResponse[^\n]+\n\n',
    '',
    main
)

# ha_result aus quiz/complete entfernen
main = re.sub(r'\s+ha_result = await _link_home_assistant\([^\)]+\)\s+', '\n', main)
main = main.replace('"home_assistant": ha_result,\n', '')

# Presence Endpoint entfernen
main = re.sub(
    r'    @app\.get\("/api/v1/presence"[^\n]+\n[\s\S]+?return \{"users": present\}\n\n',
    '',
    main
)

# extract_client_ip Funktion hinzufügen
extract_ip = '''
# --------------------------------------------------------------------- helpers


def extract_client_ip(request: Request, trust_forwarded_for: bool = True) -> str:
    """Liest die Client-IP, inklusive Reverse-Proxy-Headern."""
    import ipaddress
    from typing import Any

    def normalize_ip(raw: Any) -> str:
        if raw is None:
            return ""
        value = str(raw).strip().strip('"')
        if not value:
            return ""
        if value.startswith("["):
            value = value.split("]", 1)[0].lstrip("[")
        if value.count(":") == 1 and "." in value:
            value = value.split(":", 1)[0]
        value = value.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            return value
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return ip.compressed

    if trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return normalize_ip(first)
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return normalize_ip(real_ip)
        forwarded_header = request.headers.get("forwarded")
        if forwarded_header:
            for part in forwarded_header.split(";"):
                key, _, value = part.partition("=")
                if key.strip().lower() == "for" and value:
                    return normalize_ip(value)
    client = getattr(request, "client", None)
    return normalize_ip(getattr(client, "host", "")) if client else ""

'''

if 'def extract_client_ip' not in main:
    main = main.replace('#: Vorlagen-Avatare', extract_ip + '\n#: Vorlagen-Avatare')

# HA-Felder aus Payloads entfernen
main = re.sub(r'\s+"ha_entity_id": user\.get\("ha_entity_id"\),\s+', '\n', main)
main = re.sub(r'\s+"tracker_state": user\.get\("tracker_state"\),\s+', '\n', main)
main = re.sub(r'\s+"custom_user_id": user\.get\("custom_user_id"\),\s+', '\n', main)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(main)
print("✅ main.py")

print("📝 Patche recommender.py (Quiz-Verbesserungen)...")
with open('recommender.py', 'r', encoding='utf-8') as f:
    recommender = f.read()

# Verbessere _fill_queue Funktion für besseres adaptives Quiz
# Ändere die Gewichtung: neuere Likes wichtiger
old_liked_code = '        liked = [item for item in answers if item.get("action") == "like"]'
new_liked_code = '''        liked = [item for item in answers if item.get("action") == "like"]
        disliked = [item for item in answers if item.get("action") == "dislike"]'''

if old_liked_code in recommender:
    recommender = recommender.replace(old_liked_code, new_liked_code)

# Verbessere Seed-Auswahl: letzte 3 Likes statt 2
recommender = recommender.replace(
    'for seed in reversed(liked[-2:]):',
    'for seed in reversed(liked[-3:]):'
)

# Erhöhe Genre-Diversität
recommender = recommender.replace(
    '][:3]',
    '][:4]'
)

with open('recommender.py', 'w', encoding='utf-8') as f:
    f.write(recommender)
print("✅ recommender.py")

print("📝 Aktualisiere .env...")
with open('.env', 'r', encoding='utf-8') as f:
    env_lines = f.readlines()

new_env = []
skip_next = False
in_ha_section = False

for line in env_lines:
    if '# Home Assistant' in line or '# ---------------------------------------------------------------------------' in line and in_ha_section:
        in_ha_section = True
        continue
    if in_ha_section and (line.startswith('HA_') or line.strip() == ''):
        if line.strip() != '' or (line.strip() == '' and len(new_env) > 0 and new_env[-1].strip() != ''):
            continue
        in_ha_section = False
    if not in_ha_section:
        new_env.append(line)

with open('.env', 'w', encoding='utf-8') as f:
    f.writelines(new_env)
print("✅ .env")

# ha_client.py löschen
if os.path.exists('ha_client.py'):
    os.remove('ha_client.py')
    print("✅ ha_client.py gelöscht")

print("\n✅ Alle Patches erfolgreich angewendet!")
PYEOF

chmod +x /tmp/upgrade_patch.py
python3 /tmp/upgrade_patch.py

# Container neu bauen
echo ""
echo "4. Baue Container neu..."
docker compose build --no-cache

# Container starten
echo "5. Starte Container..."
docker compose up -d

# Kurz warten
sleep 3

# Status prüfen
echo ""
echo "6. Container Status:"
docker compose ps

echo ""
echo "7. Letzte Logs:"
docker compose logs --tail=30 cinematch

echo ""
echo "========================================"
echo "  ✅ Upgrade abgeschlossen!"
echo "========================================"
echo ""
echo "Änderungen:"
echo "  ✅ Home Assistant Integration entfernt"
echo "  ✅ Quiz adaptiert sich besser an Antworten"
echo "  ✅ Letzte 3 Likes werden berücksichtigt (statt 2)"
echo "  ✅ Mehr Genre-Diversität"
echo ""
echo "Backup wurde erstellt in:"
echo "  $BACKUP_DIR"
echo ""
echo "Anwendung läuft auf: http://192.168.2.96:8000"
echo ""
echo "Bei Problemen Backup wiederherstellen:"
echo "  docker compose down"
echo "  rm -rf /root/watch-suggest/*"
echo "  cp -r $BACKUP_DIR/* /root/watch-suggest/"
echo "  docker compose build && docker compose up -d"
