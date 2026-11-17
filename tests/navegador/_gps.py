"""GPS de mentira para os testes de tela: entrega uma leitura nova a cada 0,5 s (o app colhe até 5 leituras no check-in).
Uso: contexto.add_init_script(gps_falso(lat, lon)) antes de abrir a página. window.__gps muda a posição em tempo de teste."""
import json

_JS = """
(() => {
  const st = window.__gps = Object.assign({lat: 0, lon: 0, acc: 10, erro: 0, jitter: 2e-6, ouvindo: 0}, __INICIAL__);
  let n = 0, seq = 0; const ws = new Map();
  const leitura = () => { n++; return {coords: {latitude: st.lat + ((n % 3) - 1) * st.jitter, longitude: st.lon + (n % 2) * st.jitter,
    accuracy: st.acc + (n % 2), altitude: null, altitudeAccuracy: null, heading: null, speed: null}, timestamp: Date.now()}; };
  const entrega = (ok, err) => st.erro ? err({code: st.erro, message: 'erro simulado'}) : ok(leitura());
  Object.defineProperty(navigator, 'geolocation', {configurable: true, value: {
    watchPosition(ok, err) { const id = ++seq; setTimeout(() => entrega(ok, err), 50); ws.set(id, setInterval(() => entrega(ok, err), 500)); st.ouvindo = ws.size; return id; },
    clearWatch(id) { clearInterval(ws.get(id)); ws.delete(id); st.ouvindo = ws.size; },
    getCurrentPosition(ok, err) { setTimeout(() => entrega(ok, err), 50); }}});
})();
"""


def gps_falso(lat, lon, acc=10):
    return _JS.replace("__INICIAL__", json.dumps({"lat": lat, "lon": lon, "acc": acc}))
