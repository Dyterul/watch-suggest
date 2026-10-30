# Presence-Erkennung reparieren

## Problem
Die Ping-basierte Presence-Erkennung zeigt keine anwesenden Geräte, obwohl welche online sind.

## Diagnose auf dem Server ausführen

```bash
cd /pfad/zu/watch-suggest
chmod +x diagnose-presence.sh
./diagnose-presence.sh
```

## Häufigste Ursachen & Lösungen

### 1. Docker Container kann LAN-IPs nicht pingen (HÄUFIGSTE URSACHE)

**Problem:** Container läuft im Bridge-Netzwerk und ist vom LAN isoliert.

**Lösung A - Host Network Mode (empfohlen):**

```bash
# Backup der aktuellen docker-compose.yml
cp docker-compose.yml docker-compose.bridge.yml

# Host-Network-Variante verwenden
cp docker-compose.host-network.yml docker-compose.yml

# Container neu starten
docker compose down
docker compose up -d

# Test
curl http://localhost:8000/api/v1/presence
```

**Lösung B - Manuell in docker-compose.yml ändern:**

```yaml
services:
  cinematch:
    # ...
    network_mode: host  # Hinzufügen
    # ports: entfernen (funktioniert nicht mit host mode)
```

**WICHTIG bei network_mode: host:**
- `ports:` Mapping entfällt (Container nutzt direkt Host-Ports)
- Container lauscht weiterhin auf Port 8000
- Reverse Proxy muss auf `localhost:8000` zeigen (nicht auf Container-IP)

---

### 2. Reverse Proxy leitet Client-IP nicht weiter

**Problem:** Reverse Proxy setzt keine X-Forwarded-For Header, daher wird die Proxy-IP statt Client-IP gespeichert.

**Nginx-Konfiguration prüfen:**

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  # ← Wichtig!
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Caddy-Konfiguration:**
```
watch.kokohome.de {
    reverse_proxy localhost:8000
    # Caddy setzt X-Forwarded-For automatisch
}
```

**Traefik:**
```yaml
# Traefik setzt X-Forwarded-For automatisch
```

**Nach Änderung:**
```bash
# Nginx neu laden
sudo nginx -t && sudo systemctl reload nginx

# User müssen Onboarding wiederholen, damit neue IP gespeichert wird
```

---

### 3. User haben keine IPs gespeichert

**Problem:** User wurden angelegt, bevor Reverse Proxy korrekt konfiguriert war.

**Lösung - User-IPs manuell in Datenbank aktualisieren:**

```bash
# Datenbank öffnen
docker exec -it cinematch sqlite3 /app/data/cinematch.db

# Gespeicherte IPs prüfen
SELECT name, ip FROM users;

# IP für einen User manuell setzen (falls bekannt)
UPDATE users SET ip = '192.168.1.123' WHERE name = 'David';

# Exit
.exit
```

**Oder:** User müssen `/onboard` nochmal durchlaufen (beim Quiz-Abschluss wird IP gespeichert).

---

### 4. Geräte blockieren ICMP-Pings

**Problem:** Smartphones/Geräte haben Firewall, die Pings blockiert.

**Test auf dem Server:**

```bash
# Von Host aus pingen
ping -c 1 192.168.1.123

# Vom Container aus pingen
docker exec cinematch ping -c 1 192.168.1.123
```

**Lösungen:**
- iPhone: Keine Einstellung nötig (erlaubt Pings im WLAN)
- Android: Meistens OK, bei Custom-ROMs Private DNS deaktivieren
- Windows/Mac: Firewall prüfen
- Alternativ: Presence-Erkennung auf andere Methode umstellen (z.B. Home Assistant Integration)

---

## Schneller Test nach Änderungen

```bash
# Container neu starten
docker compose restart

# Presence-API testen
curl http://localhost:8000/api/v1/presence

# Von extern (wenn Reverse Proxy läuft)
curl https://watch.kokohome.de/api/v1/presence
```

---

## Debug-Logs aktivieren

In `.env`:
```bash
DEBUG=true
```

Dann Container neu starten:
```bash
docker compose restart
```

Logs anschauen:
```bash
docker compose logs -f cinematch
```

---

## Fallback: Explizite User-Angabe

Statt automatischer Presence-Erkennung können User explizit übergeben werden:

```bash
curl -X POST https://watch.kokohome.de/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"users":["David","Ko"],"media_type":"movie","limit":10}'
```
