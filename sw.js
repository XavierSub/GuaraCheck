/* Service worker do Guará Check (app instalável).
   O que faz: guarda uma cópia da casca do site (a página e as imagens) para a tela abrir mesmo sem sinal, no lugar do aviso de
   "sem internet" do navegador. O que NÃO faz: nunca guarda nem responde nada de /api/ (presenças, logins e chamadas vêm sempre do
   servidor), e não mexe em mapas, fontes e bibliotecas de outros endereços. Registrar presença continua exigindo conexão e GPS.
   A página é buscada na rede primeiro (quem tem sinal sempre recebe a versão mais nova); a cópia só entra quando a rede falha.
   Para forçar a troca das cópias guardadas, aumente VERSAO. */
const BASE = '__APP_PREFIX__';
const INICIO = BASE + '/';
const VERSAO = 'gc-v2-' + (BASE || 'root');
const CASCA = [INICIO, BASE + '/assets/icone-192.png', BASE + '/assets/guara-nome-branco.png', BASE + '/assets/guara-pino.png', BASE + '/assets/favicon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSAO).then(c => c.addAll(CASCA)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSAO).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

/* Página (/ e /?aula=11: é sempre o mesmo index.html): rede primeiro; a cópia guardada só quando a rede falha. Outras páginas
   do servidor (ex.: /privacidade) vão direto à rede e nunca entram nesta cópia. */
async function pagina(req) {
  try {
    const r = await fetch(req);
    const tipo = r.headers.get('content-type') || '';
    if (r.ok && tipo.startsWith('text/html')) { const c = await caches.open(VERSAO); await c.put(INICIO, r.clone()); }
    return r;
  } catch (erro) {
    const copia = await caches.match(INICIO);
    if (copia) return copia;
    throw erro;
  }
}

/* Imagens do site: a cópia guardada na hora, e uma versão nova buscada por trás para a próxima vez. */
async function imagem(req) {
  const c = await caches.open(VERSAO);
  const copia = await c.match(req);
  const rede = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => copia);
  return copia || rede;
}

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin) return;     // mapas, fontes, bibliotecas: direto da rede
  if (url.pathname.startsWith(BASE + '/api/')) return;                         // dados NUNCA vêm de cópia guardada
  if (req.mode === 'navigate' && url.pathname === INICIO) e.respondWith(pagina(req));
  else if (url.pathname.startsWith(BASE + '/assets/')) e.respondWith(imagem(req));
});
