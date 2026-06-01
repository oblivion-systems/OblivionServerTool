/**
 * api.js — typed fetch wrappers for the Oblivion Server Tool REST API.
 * All methods return a Promise that resolves to the parsed JSON body.
 * On HTTP errors the promise rejects with an Error whose message is the
 * server's `error` field (or the HTTP status text).
 */

const api = (() => {

  async function req(method, path, body = null) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== null) opts.body = JSON.stringify(body);
    const r = await fetch(path, opts);
    // Session expired / not authenticated: reload so the server shows the PIN
    // screen — except for the login call itself, whose 401 means "wrong PIN"
    // and must surface as an error the login form can display.
    if (r.status === 401 && !path.includes('/api/auth/login')) {
      location.reload();
      throw new Error('Session expired');
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const err = new Error(data.error || r.statusText); Object.assign(err, data); throw err; }
    return data;
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
