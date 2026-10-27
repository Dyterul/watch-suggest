Du bist ein Senior Python Developer und Home-Automation-Spezialist. Erstelle eine vollständige, produktionsreife Microservice-Anwendung mit **FastAPI** für ein intelligentes Gruppen-Film- und Serien-Empfehlungssystem, das mit **Home Assistant** und der **TMDB API** (The Movie Database) interagiert.

---

### 1. Architektur & Kernfunktionen

#### A. Mobile-Optimiertes Dark-Mode Onboarding mit Adaptivem Swipe-Quiz (`/onboard`)
* **Design & Styling:** Modernes, responsive UI im Dark Mode (Hintergrund `#0f172a`, Karten `#1e293b`, Akzentfarben Crimson/Indigo, abgerundete Ecken, Clean Typography, flüssige Touch-Gesten).
* **IP-Erkennung:** Automatische Erfassung der Client-IP (unter Berücksichtigung von `X-Forwarded-For` für Nginx/Reverse Proxies).
* **Onboarding-Ablauf:**
  1. **Schritt 1 (Basis-Daten):** Name (Pflichtfeld) & genutzte Streaming-Dienste (Netflix, Amazon Prime, Disney+, Apple TV+, Paramount+, Crunchyroll).
  2. **Schritt 2 (Adaptives Swipe-Quiz ~2-3 Minuten):**
     * **Tinder-Style UI:** Interaktiver Card-Stack mit Cover-Bildern, Titel, Jahr und Genre-Badges.
     * **Gesten / Buttons:** 
       * Nach rechts swipen / 👍 Button = *Gefällt mir*
       * Nach links swipen / 👎 Button = *Gefällt mir nicht*
       * Nach oben swipen / ⏭️ Button = *Nicht gesehen / Egal*
     * **Adaptive Logik:** Das Quiz startet mit 5 diversen Benchmark-Filmen. Nach jedem Swipe werden die nächsten Karten **dynamisch aus TMDB nachgeladen** (z. B. via TMDB `/movie/{id}/recommendations` für gelikte Filme oder Ausbau des bevorzugten Genre-Vektors in Echtzeit).
* **Aktion nach Absenden:**
  1. Erstellen des Geschmacksprofils (Geschmacksvektor, bewertete TMDB-IDs, Dislikes).
  2. Speichern des Nutzers in der lokalen Datenbank (SQLite oder JSON-Store).
  3. Automatische Abfrage der Home Assistant API (`/api/states`), um das `device_tracker`-Entity zu finden, welches zu dieser Handy-IP gehört.
  4. Verknüpfung der `custom_user_id` im Home Assistant Tracker-State via HA-REST-API.

#### B. Passwortgeschütztes Admin Dashboard (`/admin`)
* **Zugangsschutz:** HTTP Basic Auth oder Session-Cookie basierend auf Umgebungsvariable `ADMIN_PASSWORD`.
* **Features:**
  1. **Haushalts-Abonnements:** Checkboxen/Toggles zur Verwaltung der im Haushalt **aktuell aktiv abonnierten Streaming-Dienste** (Netflix, Prime, Disney+, Apple TV+, Paramount+, Crunchyroll).
  2. **Nutzer- & Profilbild-Verwaltung:**
     * Übersicht aller registrierten Personen und deren IP-Adressen.
     * **Profilbild-Upload:** Möglichkeit, für jede Person ein Profilbild (Avatar) hochzuladen oder ein Bild aus einer Vorlage auszuwählen.
     * Speicherung der Avatare unter `/static/avatars/` und Auslieferung über die API.
     * Löschen oder Zurücksetzen von Nutzerprofilen.

#### C. Home Assistant Presence Tracking & API
* **Endpoint `/api/v1/recommendations` (POST):**
  * Akzeptiert ein JSON mit `users: List[str]` oder fragt alternativ bei HA alle `device_tracker` mit `state == 'home'` ab, die registrierten Personen zugeordnet sind.
  * Unterstützt Parameter `media_type` (`movie` oder `tv`) und `limit` (Default: 10).
  * **Response-Daten:** Enthält neben Titel, Rating, Overview und Poster-Path auch die Profilbilder (`avatar_url`) der anwesenden Nutzer, für die das Suchergebnis optimiert wurde.

#### D. Empfehlungs-Engine ("Least Misery" & Strikte Provider-Filterung)
1. **Provider-Schnittmenge:**
   * Empfohlene Titel dürfen **ausschließlich** von Streaming-Anbietern stammen, die im Admin-Dashboard (`/admin`) aktuell als **aktiv aboniert** markiert sind UND von mindestens einer anwesenden Person genutzt werden.
2. **Aggregieren & Filtern:**
   * Strikter Ausschluss aller Titel, die bereits von mindestens einer anwesenden Person als gesehen oder negativ bewertet wurden.
   * Berechnung der Genre- und Geschmacksgewichtung basierend auf den Swipes aus dem Quiz.
3. **TMDB Query:** Abfrage der `/discover/movie` bzw. `/discover/tv` Endpoints von TMDB mit Filtern für Streaming-Region (DE), Provider-IDs und Genres.
4. **Scoring:** Sortierung der Ergebnisse nach Bewertung (`vote_average`) und Popularität.

---

### 2. Technische Vorgaben

* **Tech Stack:** Python 3.11+, FastAPI, `httpx` (asynchrone Requests), `pydantic` (V2), SQLite / `tinydb`, HTML5 Touch-Events / CSS Transform für Tinder-Swiping.
* **TMDB Provider IDs (Region DE):**
  * Netflix: `8`, Amazon Prime: `119`, Disney+: `337`, Apple TV+: `350`, Paramount+: `531`, Crunchyroll: `283`
* **Dateistruktur:**
  * `main.py` (FastAPI Endpoints, HTML Static-Mounting, Dynamic Quiz-Fetch Endpoints)
  * `config.py` (Environment Variable Parsing via `pydantic-settings`)
  * `recommender.py` (Adaptive Swipe-Logik, TMDB Ingest & Least-Misery Engine)
  * `ha_client.py` (HA REST-API Interaktion & IP Matching)
  * `static/` (CSS/JS für Swiping-Gesten, Avatare)
  * `requirements.txt`
  * `Dockerfile` & `docker-compose.yml`
  * Inline-Dokumentation für `configuration.yaml` (HA REST-Command).

---

### 3. Deliverables

Erstelle den vollständigen, funktionsfähigen Code für alle Dateien ohne Platzhalter oder abgekürzte Abschnitte. Achte darauf, dass die Touch-Events (Swipe-Karten) auf dem Handy flüssig laufen und das Nachladen neuer Empfehlungskarten während des Quizzes über einen `/api/v1/quiz/next`-Endpoint in Echtzeit erfolgt.
