#!/bin/bash
# Diagnose-Script für Presence-Probleme

echo "=== CineMatch Presence Diagnose ==="
echo ""

echo "1. Admin-API: Gespeicherte User mit IPs"
echo "----------------------------------------"
curl -s -u admin:$(grep ADMIN_PASSWORD .env | cut -d= -f2) \
  http://localhost:8000/admin/api/users | python3 -m json.tool 2>/dev/null || \
  curl -s -u admin:$(grep ADMIN_PASSWORD .env | cut -d= -f2) http://localhost:8000/admin/api/users
echo ""
echo ""

echo "2. Docker Container Netzwerk-Modus"
echo "-----------------------------------"
docker inspect cinematch --format='Network Mode: {{.HostConfig.NetworkMode}}'
echo ""

echo "3. Test: Kann der Container pingen?"
echo "------------------------------------"
echo "Teste Ping von innerhalb des Containers..."
docker exec cinematch ping -c 1 -W 1 192.168.1.1 > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo "✓ Container kann Gateway (192.168.1.1) pingen"
else
    echo "✗ Container kann Gateway NICHT pingen"
fi
echo ""

echo "4. Gespeicherte IPs aus der Datenbank"
echo "--------------------------------------"
docker exec cinematch sqlite3 /app/data/cinematch.db \
  "SELECT name, ip, created_at FROM users;" 2>/dev/null
echo ""

echo "5. X-Forwarded-For Test"
echo "-----------------------"
echo "TRUST_FORWARDED_FOR Einstellung:"
grep TRUST_FORWARDED_FOR .env || echo "TRUST_FORWARDED_FOR=true (default)"
echo ""

echo "6. Presence API direkter Test"
echo "------------------------------"
curl -s http://localhost:8000/api/v1/presence | python3 -m json.tool 2>/dev/null || \
  curl -s http://localhost:8000/api/v1/presence
echo ""
echo ""

echo "=== Lösungsvorschläge ==="
echo ""
echo "Problem 1: IPs sind leer oder öffentliche IPs"
echo "  → Reverse Proxy muss X-Forwarded-For Header setzen"
echo "  → Nginx: proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
echo ""
echo "Problem 2: Container kann nicht pingen"
echo "  → Option A: network_mode: host in docker-compose.yml"
echo "  → Option B: Docker-Netzwerk mit --network host starten"
echo ""
echo "Problem 3: User haben keine gespeicherten IPs"
echo "  → User müssen /onboard nochmal durchlaufen"
echo "  → Bei Abschluss wird die IP automatisch gespeichert"
