/**
 * api.js — typed fetch wrappers for the Oblivion Server Tool REST API.
 * All methods return a Promise that resolves to the parsed JSON body.
 * On HTTP errors the promise rejects with an Error whose message is the
 * server's `error` field (or the HTTP status text).
 */

const api = (() => {

  // v0.10.2 — request bottleneck with timeout + one retry on network error.
  //
  // Why: the cross-cutting audit caught that a single network blip on a
  // Cloudflare-tunnelled session = silent stuck UI.  Default `fetch` has
  // no timeout, and a one-off DNS/cell-handoff hiccup would leave the
  // user with no feedback either way.
  //
  // Strategy:
  //   - 10 s AbortController timeout per attempt.
  //   - One retry ONLY on network-level failure (fetch threw, OR HTTP 502/503/504
  //     which on Cloudflare means "tunnel briefly lost upstream").
  //     We do NOT retry 4xx — those are intentional rejections that
  //     re-tries can't fix.
  //   - On final failure, throw a useful Error with: .status (number or 0),
  //     .body (parsed JSON if any), .network (true for fetch-level failures
  //     so the SPA can render a "lost connection" banner instead of a
  //     plain toast).
  const REQ_TIMEOUT_MS = 10_000;
  const RETRY_STATUSES = new Set([502, 503, 504]);

  async function _attempt(method, path, body, abortSignal) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
      signal:  abortSignal,
    };
    if (body !== null) opts.body = JSON.stringify(body);
    return await fetch(path, opts);
  }

  async function req(method, path, body = null) {
    let lastErr = null;
    // Two attempts max: first try, then one retry on transient failure.
    for (let attempt = 0; attempt < 2; attempt++) {
      const ctrl  = new AbortController();
      const timer = setTimeout(() => ctrl.abort('timeout'), REQ_TIMEOUT_MS);
      let r;
      try {
        r = await _attempt(method, path, body, ctrl.signal);
      } catch (fetchErr) {
        // fetch threw — network-level failure (DNS, refused, abort, etc.).
        clearTimeout(timer);
        lastErr = fetchErr;
        if (attempt === 0) {
          // Brief pause before retry — gives the tunnel time to reconnect
          await new Promise(res => setTimeout(res, 400));
          continue;
        }
        const err = new Error(`Network error: ${fetchErr.message || fetchErr}`);
        err.status = 0;
        err.network = true;
        throw err;
      } finally {
        clearTimeout(timer);
      }

      // Session expired / not authenticated: reload so the server shows the PIN
      // screen — except for the login call itself, whose 401 means "wrong PIN"
      // and must surface as an error the login form can display.
      if (r.status === 401 && !path.includes('/api/auth/login')) {
        location.reload();
        throw new Error('Session expired');
      }

      // Cloudflare-tunnel hiccup? retry once.
      if (RETRY_STATUSES.has(r.status) && attempt === 0) {
        lastErr = new Error(`HTTP ${r.status}`);
        await new Promise(res => setTimeout(res, 400));
        continue;
      }

      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const err = new Error(data.error || r.statusText);
        err.status = r.status;
        err.body   = data;
        Object.assign(err, data);   // preserve old behaviour: err.preflight_errors, etc.
        throw err;
      }
      return data;
    }
    // Shouldn't reach here, but defend against logic bugs.
    throw lastErr || new Error('Request failed');
  }

  const get  = (path)        => req('GET',    path);
  const post = (path, body)  => req('POST',   path, body || {});
  const del  = (path, body)  => req('DELETE', path, body);

  return {
    // ── Auth ──────────────────────────────────────────────────────────────
    login:   (pin)      => post('/api/auth/login',  { pin }),
    logout:  ()         => post('/api/auth/logout'),

    // ── State ─────────────────────────────────────────────────────────────
    state:   ()         => get('/api/state'),

    // ── Capabilities (v0.10.2) — {role, is_local, can: [tags]} ────────────
    // Single source of truth for "what can the current session do."
    // SPA renders local-only / admin-only controls disabled-with-tooltip
    // based on this instead of try-then-403.
    capabilities: ()    => get('/api/capabilities'),

    // ── Server control ────────────────────────────────────────────────────
    start:   (map, mode, workshop = false) =>
                          post('/api/server/start', { map, mode, workshop }),
    stop:    ()         => post('/api/server/stop'),
    map:     (map, mode, workshop = false) =>
                          post('/api/server/map',   { map, mode, workshop }),
    broadcast: (message) => post('/api/server/broadcast', { message }),
    ff:      (enabled)  => post('/api/server/ff',          { enabled }),
    restartRound: ()    => post('/api/server/round/restart'),
    endWarmup:    ()    => post('/api/server/round/warmup'),
    pause:        ()    => post('/api/server/match/pause'),
    unpause:      ()    => post('/api/server/match/unpause'),

    // ── Bots ──────────────────────────────────────────────────────────────
    addBots: (count)    => post('/api/bots/add',  { count }),
    kickBots:()         => post('/api/bots/kick'),

    // ── Players ───────────────────────────────────────────────────────────
    players: ()         => get('/api/players'),
    kick:    (userid, name) => post('/api/players/kick', { userid, name }),
    ban:     (steamid, name, duration = 0) =>
                          post('/api/players/ban',  { steamid, name, duration }),
    bans:    ()         => get('/api/bans'),
    unban:   (steamid)  => post('/api/bans/remove', { steamid }),

    // ── Config ────────────────────────────────────────────────────────────
    config:    ()       => get('/api/config'),
    setConfig: (data)   => post('/api/config', data),

    // ── Presets ───────────────────────────────────────────────────────────
    presets:       ()       => get('/api/presets'),
    savePreset:    (name)   => post('/api/presets/save', { name }),
    loadPreset:    (name)   => post('/api/presets/load', { name }),
    deletePreset:  (name)   => del(`/api/presets/${encodeURIComponent(name)}`),

    // ── RCON ──────────────────────────────────────────────────────────────
    rcon:    (command)  => post('/api/rcon', { command }),

    // ── Workshop ──────────────────────────────────────────────────────────
    workshopMaps:     ()    => get('/api/workshop/maps'),
    workshopDownload: (id)  => post('/api/workshop/download', { id }),
    workshopCancel:   ()    => post('/api/workshop/cancel'),
    workshopUpdate:   ()    => post('/api/workshop/update'),
    workshopCmdfilterScan:     ()          => post('/api/workshop/cmdfilter/scan'),
    workshopCmdfilterOverride: (id, value) =>
                          post('/api/workshop/cmdfilter/override', { id, value }),
    requestWorkshop:  (workshop_id) =>
                          post('/api/request_workshop', { workshop_id }),

    // ── Server install / update ───────────────────────────────────────────
    install:   ()       => post('/api/server/install'),
    updateCs2: ()       => post('/api/server/update_cs2'),

    // ── Steam ─────────────────────────────────────────────────────────────
    steamLogin: ()      => post('/api/steam/login'),

    // ── System ────────────────────────────────────────────────────────────
    pickDirectory: ()   => get('/api/system/pick_directory'),

    // ── Game data ─────────────────────────────────────────────────────────
    modes:            ()  => get('/api/data/modes'),
    maps:             ()  => get('/api/data/maps'),
    modeMaps:         ()  => get('/api/data/mode_maps'),
    modeWorkshopTags: ()  => get('/api/data/mode_workshop_tags'),

    // ── Setup (first-run) ─────────────────────────────────────────────────
    setupStatus:   ()     => get('/api/setup/status'),
    setupComplete: (data) => post('/api/setup/complete', data),

    // ── Log ───────────────────────────────────────────────────────────────
    logHistory: ()      => get('/api/log/history'),
    logSave:    ()      => post('/api/log/save'),

    // ── Veto (v0.10.0) ─────────────────────────────────────────────────
    veto: {
      state:      ()                 => get('/api/veto/state'),
      create:     (mode, mapPool)    => post('/api/veto/create', { mode, map_pool: mapPool }),
      roster:     (teamA, teamB, ps) => post('/api/veto/roster', {
                                          team_a_name: teamA, team_b_name: teamB, players: ps }),
      distribute: ()                 => post('/api/veto/distribute'),
      startVoting:()                 => post('/api/veto/start_voting'),
      vote:       (team, vi, ti)     => post('/api/veto/vote', {
                                          team, voter_idx: vi, votee_idx: ti }),
      resolve:    ()                 => post('/api/veto/resolve_captains'),
      tokens:     ()                 => post('/api/veto/tokens'),
      revokeToken:(team)             => post('/api/veto/revoke_token', { team }),
      claim:      (token)            => post('/api/veto/claim', { token }),
      step:       (team, mapId)      => post('/api/veto/step', { team, map_id: mapId }),
      finale:     (loadMatch=true)   => post('/api/veto/finale', { load_match: loadMatch }),
      reset:      ()                 => post('/api/veto/reset'),
      // v0.10.2: rematch with same teams.  Optional body fields:
      // mode ('BO1'|'BO3'|'BO5') + map_pool (7 entries).
      rematch:    (mode, mapPool)    => post('/api/veto/rematch',
                                          mode || mapPool ? { mode, map_pool: mapPool } : {}),
      // v0.10.1: captain ready toggle.  team is OPTIONAL — captain role
      // sessions infer it from their cookie; admin must pass it explicitly.
      ready:      (ready, team)      => post('/api/veto/ready',
                                          team ? { ready, team } : { ready }),
      // QR returns SVG bytes, not JSON — used only as <img src=…>.
      // qrUrl() builds the URL the SPA embeds; no fetch wrapper needed.
      qrUrl:      (token, kind='lan') =>
                    `/api/veto/qr?token=${encodeURIComponent(token)}&kind=${encodeURIComponent(kind)}`,
    },
  };
})();
