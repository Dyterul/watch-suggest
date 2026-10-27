# CineMatch

Gruppen-Filmempfehlungen für den Haushalt: ein adaptives Swipe-Quiz auf dem Handy,
Home-Assistant-Presence-Tracking und eine Least-Misery-Engine, die nur Titel
empfiehlt, die wirklich bei einem aktiven Abo laufen.

**Stack:** Python 3.11+, FastAPI, httpx, Pydantic v2, SQLite, Vanilla JS mit
Pointer-Gesten (kein Framework, kein Build-Schritt).

---

## Was drin ist

| Bereich | Funktion |
| --- | --- |
| `/onboard` | Mobile Dark-Mode-Onboarding, Name + Streaming-Dienste, Tinder-Swipe-Quiz |
| Adaptives Quiz | Start mit 5 diversen Benchmark-Filmen, danach TMDB-Empfehlungen zu Likes + Genre-Vektor in Echtzeit über `/api/v1/quiz/next` |
| `/admin` | HTTP-Basic-Auth, Haushalts-Abos, alle Profile mit IPs, Avatar-Upload, Vorlagen, Reset/Löschen |
| `/api/v1/recommendations` | Gruppenempfehlungen für übergebene Personen oder automatisch alle per HA anwesenden Personen |
| Presence | `/api/v1/presence`, IP-Matching gegen `device_tracker`, `custom_user_id`-Verknüpfung |
| Engine | Strikte Provider-Schnittmenge, Ausschluss bereits bewerteter Titel, Genre-/Taste-Vektor, Least-Misery-Score |

### Dateien

```
main.py            FastAPI-App, Seiten, Quiz- und Admin-Endpunkte
config.py          pydantic-settings, Provider-Katalog, TMDB-Region
store.py           SQLite-Repository (Nutzer, Sessions, Haushalts-Einstellungen)
recommender.py     TMDB-Client, adaptives Quiz, Least-Misery-Engine
ha_client.py       HA-REST-Client, IP-Erkennung, Tracker-Matching
static/            HTML, CSS, JS, Profilbild-Vorlagen, Uploads
Dockerfile         Produktions-Image (non-root)
docker-compose.yml Persistente SQLite-DB + Avatar-Volume
```

---

## Schnellstart

### Docker Compose (empfohlen)

```bash
cp .env.example .env
# .env öffnen: TMDB_API_KEY (oder TMDB_READ_ACCESS_TOKEN), ADMIN_PASSWORD,
# HA_BASE_URL und HA_TOKEN setzen
docker compose up -d --build
```

Danach:

* Onboarding: <http://localhost:8000/onboard>
* Admin: <http://localhost:8000/admin> (Browser fragt nach Basic-Auth)
* API-Doku: <http://localhost:8000/api/docs>
* Health: <http://localhost:8000/api/v1/health>

Daten liegen in den Named Volumes `cinematch-data` (SQLite) und
`cinematch-avatars` (Uploads). Backup:

```bash
docker run --rm -v cinematch-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/cinematch-data.tgz -C /data .
```

### Lokal

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Das Verzeichnis `data/` und `static/avatars/uploads/` werden automatisch angelegt.
Die SQLite-DB läuft im WAL-Modus.

---

## Konfiguration

Alle Variablen können in `.env` oder als echte Umgebungsvariablen gesetzt werden
(pydantic-settings, Groß-/Kleinschreibung egal).

| Variable | Default | Bedeutung |
| --- | --- | --- |
| `ADMIN_USER` | `admin` | Basic-Auth-Benutzer für `/admin` |
| `ADMIN_PASSWORD` | *(leer)* | Ohne Passwort ist `/admin` mit 503 gesperrt |
| `TMDB_API_KEY` | *(leer)* | TMDB v3 API-Key |
| `TMDB_READ_ACCESS_TOKEN` | *(leer)* | Alternativ v4 Read Access Token (Bearer) |
| `TMDB_REGION` | `DE` | Watch-Region für Discover/Provider |
| `TMDB_LANGUAGE` | `de-DE` | Sprache für Titel/Overviews |
| `HA_BASE_URL` | *(leer)* | z. B. `http://homeassistant.local:8123` |
| `HA_TOKEN` | *(leer)* | Long-Lived Access Token |
| `HA_LINK_SERVICE` | `script.link_tracker_user` | Script, das `custom_user_id` in den Tracker schreibt |
| `TRUST_FORWARDED_FOR` | `true` | `X-Forwarded-For` auswerten (Nginx/Reverse Proxy) |
| `HOUSEHOLD_DEFAULT_PROVIDERS` | `netflix,prime` | Startwert, bis im Admin gespeichert wird (Komma oder JSON) |
| `QUIZ_MAX_ANSWERS` | `20` | Maximale Karten pro Session |
| `MAX_AVATAR_BYTES` | `3145728` | Upload-Limit für Avatare (3 MB) |

**TMDB-Key holen:** Kostenloses Konto auf <https://www.themoviedb.org/signup>,
dann *Settings → API* → API-Key beantragen. Der Key wird ausschließlich
serverseitig verwendet und nie an den Browser ausgeliefert.

**Home-Assistant-Token:** Profil → *Long-Lived Access Tokens* → Token erstellen.
Der Token bleibt in `.env` bzw. in den Secrets der Deployment-Umgebung.

---

## Home Assistant einrichten

### 1. Long-Lived Token und Erreichbarkeit

CineMatch muss die HA-Instanz im LAN erreichen (`HA_BASE_URL`) und der Token
braucht mindestens Lesezugriff auf States sowie Aufrufrechte für das
Verknüpfungs-Script.

### 2. `device_tracker` mit IP-Attribut

Beim Onboarding sucht der Service in `GET /api/states` nach
`device_tracker.*`-Entities, deren Attribut `ip`, `ip_address`, `current_ip`
oder `last_ip` zur Client-IP passt. Viele Router-Integrationen (z. B. FRITZ!Box
Tools, Unifi, Ping) liefern diese Attribute. Prüfen unter *Entwicklerwerkzeuge →
Zustände*: Das Attribut muss auf der Tracker-Entity sichtbar sein.

Ohne IP-Attribut schlägt nur die automatische Zuordnung fehl; Profile und
Empfehlungen funktionieren weiter (z. B. mit expliziter `users`-Liste). Sobald
eine Person einmal zugeordnet ist, merkt sich CineMatch `ha_entity_id` und
`custom_user_id` und findet den Tracker auch ohne IP wieder.

### 3. `configuration.yaml` – REST-Command für HA → CineMatch

```yaml
# configuration.yaml
rest_command:
  cinematch_recommendations:
    url: "http://192.168.1.10:8000/api/v1/recommendations"
    method: POST
    content_type: "application/json"
    timeout: 30
    payload: >-
      {"media_type": "{{ media_type | default('movie') }}",
       "limit": {{ limit | default(10) }}}
```

Aufruf aus einer Automatisierung oder einem Script:

```yaml
script:
  filmabend_empfehlung:
    alias: "Filmabend – CineMatch"
    sequence:
      - service: rest_command.cinematch_recommendations
        data:
          media_type: movie
          limit: 10
```

`rest_command` gibt die Antwort nicht als Entity zurück. Für Anzeige in Lovelace
nutzt du zusätzlich einen REST-Sensor (lädt automatisch die anwesenden Personen):

```yaml
sensor:
  - platform: rest
    name: CineMatch Empfehlungen
    unique_id: cinematch_empfehlungen
    resource: "http://192.168.1.10:8000/api/v1/recommendations"
    method: POST
    headers:
      Content-Type: application/json
    payload: '{"media_type": "movie", "limit": 5}'
    value_template: "{{ value_json.results[0].title if value_json.results else 'Keine Empfehlung' }}"
    json_attributes:
      - results
      - present_users
      - active_providers
    scan_interval: 900
```

Benachrichtigung beim Start des Filmabends:

```yaml
automation:
  - alias: "Filmabend – Empfehlung anzeigen"
    trigger:
      - platform: state
        entity_id: input_boolean.filmabend
        to: "on"
    action:
      - service: homeassistant.update_entity
        target:
          entity_id: sensor.cinematch_empfehlungen
      - service: persistent_notification.create
        data:
          title: "CineMatch"
          message: >-
            {% set results = state_attr('sensor.cinematch_empfehlungen', 'results') %}
            {% if results %}
              {{ results[0].title }} ({{ results[0].year }}) ·
              {{ results[0].match_percent }} % Match · {{ results[0].reason }}
            {% else %}
              Keine passende Empfehlung – Abos oder Profile prüfen.
            {% endif %}
```

### 4. `custom_user_id` in den Tracker schreiben

Home Assistant kann Attribute von `device_tracker`-Entities nicht über die
generische REST-API setzen. Deshalb ruft CineMatch einen Script-Service auf,
der ein `python_script` nutzt. `python_script` ist ein eingebauter Bestandteil
von Home Assistant, muss aber aktiviert werden.

`configuration.yaml`:

```yaml
# Aktiviert python_scripts (einmalig, danach HA neu starten)
python_script:

script:
  link_tracker_user:
    alias: "CineMatch: Tracker mit Profil verknüpfen"
    description: "Schreibt custom_user_id in die Attribute eines device_tracker."
    mode: queued
    fields:
      entity_id:
        description: "device_tracker-Entity"
        example: "device_tracker.lea_phone"
      custom_user_id:
        description: "CineMatch-Profil-ID"
        example: "cinematch-1a2b3c4d"
    sequence:
      - service: python_script.link_tracker_user
        data:
          entity_id: "{{ entity_id }}"
          custom_user_id: "{{ custom_user_id }}"
```

Datei `config/python_scripts/link_tracker_user.py` anlegen:

```python
entity_id = data.get("entity_id")
custom_user_id = data.get("custom_user_id")

if not entity_id or not custom_user_id:
    logger.error("CineMatch: entity_id und custom_user_id sind erforderlich")
elif (state := hass.states.get(entity_id)) is None:
    logger.error("CineMatch: Entity %s existiert nicht", entity_id)
else:
    attributes = dict(state.attributes)
    attributes["custom_user_id"] = custom_user_id
    hass.states.async_set(entity_id, state.state, attributes, force_update=True)
    logger.info("CineMatch: %s -> %s verknüpft", entity_id, custom_user_id)
```

CineMatch ruft beim Abschluss des Onboardings
`POST /api/services/script/link_tracker_user` mit `entity_id` und
`custom_user_id` auf. Der Tracker-Name kann über `HA_LINK_SERVICE` geändert
werden (Format `domain.service`).

> Hinweis: Die Integration, die den Tracker besitzt, kann Attribute beim
> nächsten Update überschreiben. Für die Presence-Erkennung ist das unkritisch,
> weil CineMatch die Zuordnung zusätzlich in der eigenen SQLite-DB speichert
> (`ha_entity_id`). Für dauerhaft sichtbare Attribute in HA empfiehlt sich ein
> Template-Sensor auf Basis der CineMatch-API.

---

## API

Interaktive Doku: `/api/docs` (OpenAPI).

### Health

```bash
curl http://localhost:8000/api/v1/health
```

```json
{
  "status": "ok",
  "tmdb_configured": true,
  "home_assistant_configured": true,
  "admin_configured": true,
  "users": 3,
  "region": "DE"
}
```

### Onboarding-Ablauf

```bash
# 1) Session + 5 Benchmark-Karten
curl -s -X POST http://localhost:8000/api/v1/quiz/start \
  -H 'Content-Type: application/json' \
  -d '{"name":"Lea","providers":["netflix","disney"]}'

# 2) Swipe speichern (like | dislike | skip)
curl -s -X POST http://localhost:8000/api/v1/quiz/swipe \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<ID>","tmdb_id":27205,"media_type":"movie","action":"like"}'

# 3) Nächste Karten (adaptiv nachgeladen)
curl -s -X POST http://localhost:8000/api/v1/quiz/next \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<ID>","limit":3}'

# 4) Profil anlegen + HA-Tracker verknüpfen
curl -s -X POST http://localhost:8000/api/v1/quiz/complete \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<ID>"}'
```

Die Client-IP wird aus `X-Forwarded-For` (erster Eintrag), `X-Real-IP` oder dem
Socket-Peer gelesen und beim Abschluss am Profil gespeichert.

### Empfehlungen

```bash
# Automatisch: alle registrierten Personen, deren Tracker "home" ist
curl -s -X POST http://localhost:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"media_type":"movie","limit":10}'

# Explizit: Namen oder Profil-IDs
curl -s -X POST http://localhost:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"users":["Lea","Jonas"],"media_type":"tv","limit":5}'
```

Antwort (gekürzt):

```json
{
  "media_type": "movie",
  "presence_source": "home_assistant",
  "present_users": [
    {
      "id": "…",
      "name": "Lea",
      "avatar_url": "/static/avatars/uploads/lea.jpg",
      "providers": ["netflix"],
      "provider_names": ["Netflix"],
      "source": "home_assistant",
      "ha_entity_id": "device_tracker.lea_phone",
      "tracker_state": "home"
    }
  ],
  "active_providers": [{"slug": "netflix", "name": "Netflix"}],
  "household_providers": [{"slug": "netflix", "name": "Netflix"}],
  "excluded_titles": 4,
  "candidates_checked": 38,
  "genre_filter": ["Drama", "Science Fiction"],
  "results": [
    {
      "tmdb_id": 603,
      "media_type": "movie",
      "title": "Matrix",
      "year": 1999,
      "overview": "…",
      "poster_path": "/f89U3ADr1oiB1s9GkdPOEpXUk5H.jpg",
      "poster_url": "https://image.tmdb.org/t/p/w500/…",
      "vote_average": 8.2,
      "genres": ["Action", "Science Fiction"],
      "providers": ["Netflix"],
      "match_percent": 78,
      "score": 0.7812,
      "reason": "Bester Kompromiss für 2 Personen · Action · auf Netflix"
    }
  ]
}
```

Fehlercodes mit sprechender `detail`-Meldung:

* `409` – keine Provider-Schnittmenge oder kein aktives Haushalts-Abo
* `404` – unbekannte Nutzer / niemand anwesend
* `502` – TMDB oder Home Assistant nicht erreichbar

### Presence

```bash
curl http://localhost:8000/api/v1/presence
```

Liefert alle registrierten Personen, deren zugeordneter `device_tracker`
aktuell `home` meldet.

---

## Empfehlungslogik

1. **Aktive Dienste** = Haushalts-Abos aus `/admin` ∩ Dienste der anwesenden
   Personen. Keine Schnittmenge → keine Empfehlung (409).
2. **Kandidaten** über `GET /discover/{movie|tv}` mit
   `watch_region=DE`, `with_watch_providers=<IDs>`,
   `with_watch_monetization_types=flatrate`, optional Genre-Filter aus dem
   aggregierten Geschmacksvektor.
3. **Strikte Prüfung:** Jeder Kandidat wird zusätzlich über
   `/{media_type}/{id}/watch/providers` geprüft. Nur Titel, die in der Region
   DE als Flatrate-Angebot eines aktiven Haushalts-Dienstes gelistet sind,
   bleiben übrig. Der Genrefilter wird gelockert, falls zu wenige Treffer
   übrig bleiben – die Provider-Prüfung nie.
4. **Ausschluss:** Titel, die eine anwesende Person gesehen, geliked oder
   dislikes hat, fliegen raus (Union über alle Anwesenden).
5. **Taste-Vektor:** Aus den Swipes: Like `+1.0`, Dislike `-0.65`,
   „Kenne ich nicht“ `0`. Pro Person auf das Maximum normiert; Film-Genres
   werden für Serien auf die TV-Genre-IDs gemappt.
6. **Least Misery:** Für jeden Titel wird pro Person eine Affinität aus
   Genre-Vektor und Bewertung berechnet. Der Score gewichtet die unzufriedenste
   Person am stärksten:

   ```
   score = 0.52 * min(user_scores)
         + 0.26 * mean(user_scores)
         + 0.14 * (vote_average / 10)
         + 0.08 * popularity_norm
   ```

7. Sortierung nach Score, dann `vote_average`, dann `popularity`.
   `match_percent = round(score * 100)`.

Der Quiz-Vektor wird nach jedem Swipe serverseitig neu berechnet. Die nächsten
Karten kommen zuerst aus `/movie/{id}/recommendations` der zuletzt gelikten
Titel, dann aus `/discover/movie` entlang der positiv gewichteten Genres, zuletzt
aus populären Titeln. Bereits gesehene Karten werden dedupliziert.

---

## Admin-Dashboard

* **Haushalts-Abos:** sechs Toggles. Die Auswahl wird sofort gespeichert und
  gilt für alle folgenden Empfehlungen.
* **Personen:** Name, IP, Dienste, Swipe-Zahlen, Top-Genres, HA-Tracker.
* **Profilbild:** Upload (PNG/JPEG/WebP, max. 3 MB) oder eine von sechs
  Vorlagen. Uploads landen unter `static/avatars/uploads/`, Vorlagen unter
  `static/avatars/`.
* **Zurücksetzen:** löscht Swipes/Geschmack, behält Avatar und HA-Zuordnung.
* **Löschen:** entfernt Profil und hochgeladenes Bild.

Die Seite ist per HTTP Basic Auth geschützt. Ohne `ADMIN_PASSWORD` antworten
`/admin` und alle `/admin/api/*` mit `503`.

---

## Reverse Proxy (Beispiel Nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name cinematch.example.de;

    ssl_certificate     /etc/letsencrypt/live/cinematch.example.de/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cinematch.example.de/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`TRUST_FORWARDED_FOR=true` setzen, damit die echte Handy-IP ankommt. Nur so
funktioniert das IP-Matching gegen die `device_tracker`.

CineMatch hat keinen eigenen Login für `/onboard` und `/api/v1/*` – das ist für
den Betrieb im Heimnetz gedacht. Wenn du die API ins Internet stellst, schütze
sie zusätzlich auf Proxy-Ebene (z. B. `auth_basic` für alles außer `/static/`
und `/onboard`, oder ein VPN). Die Empfehlungs-API liefert Namen und Avatare
der anwesenden Personen.

---

## Betrieb

* **Datenbank:** `data/cinematch.db` (SQLite, WAL). Backup im laufenden Betrieb
  über `sqlite3 data/cinematch.db ".backup 'backup.db'"`.
* **Avatare:** `static/avatars/uploads/`. Presets liegen im Image und sind
  schreibgeschützt.
* **Quiz-Sessions:** werden nach 48 Stunden automatisch aufgeräumt.
* **TMDB-Cache:** In-Memory, TTL 15 Minuten (konfigurierbar). Kein externer
  Cache nötig.
* **Logs:** uvicorn-Standardausgabe. Konfigurationsprobleme (fehlender
  TMDB-Key, fehlendes Admin-Passwort, fehlende HA-Zugänge) werden beim Start
  geloggt.

### Bekannte Grenzen

* Die automatische Tracker-Zuordnung braucht ein IP-Attribut am
  `device_tracker`. Alternativ die Person einmal über die API mit
  `ha_entity_id`/IP verknüpfen – danach greift die gespeicherte Zuordnung.
* Die HA-Verknüpfung schreibt Attribute per `python_script`, weil die REST-API
  keine generischen State-Attribute setzen kann. Das Script muss installiert
  sein (`HA_LINK_SERVICE`).
* `discover` liefert pro Genre-Filter maximal 100 Seiten; CineMatch prüft
  bewusst nur die ersten Seiten und dafür jeden Kandidaten strikt gegen die
  DE-Flatrate-Anbieter.
* TMDB-Daten für die Region DE sind nicht immer vollständig. Titel ohne
  Flatrate-Eintrag werden ausgeschlossen – lieber weniger, dafür korrekt.

---

## Lizenz / Credits

Dieses Projekt nutzt die TMDB-API, ist aber nicht von TMDB endorsed oder
zertifiziert. Film-Metadaten und Bilder: © The Movie Database (TMDB).
