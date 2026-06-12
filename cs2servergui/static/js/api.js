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

    // ── Discord (v0.11.0 Layer 1B — voice-channel roster pull) ────────────
    discord: {
      voiceChannels: ()           => get('/api/discord/voice_channels'),
      voiceMembers:  (channelId)  => get(`/api/discord/voice_members?channel_id=${encodeURIComponent(channelId)}`),
      // v0.11.18 — text-channel list for the Veto Embed Channel ID picker
      textChannels:  ()           => get('/api/discord/text_channels'),
      // v0.11.15 — single-VC live info (id, name, member_count).  Optional
      // channelId; omit to use the configured discord_voice_channel_id.
      voiceChannelInfo: (channelId) => get(
        '/api/discord/voice_channel_info' +
        (channelId ? `?channel_id=${encodeURIComponent(channelId)}` : '')
      ),
      // v0.12.0 — bot-driven team voice splits.  moveTeams() reads the
      // active veto session's team_a/team_b discord_ids and drags every
      // rostered player into their team's configured VC.  Requires both
      // discord_team_a_voice_channel_id AND discord_team_b_voice_channel_id
      // configured.  autoMoveToggle({enabled}) persists the auto-fire
      // toggle so `/api/veto/distribute` fires moveTeams in a background
      // thread after every team split.
      moveTeams:        ()        => post('/api/discord/move_teams', {}),
      autoMoveToggle:   (enabled) => post('/api/discord/auto_move_toggle', { enabled: !!enabled }),
      // v0.12.1 — round summaries (per-round embed in the veto channel
      // during a live MatchZy match)
      roundSummariesToggle: (enabled) => post('/api/discord/round_summaries_toggle', { enabled: !!enabled }),
      // v0.11.0 polish — connection-check helpers
      testEmbed:     (channelId)  => post('/api/discord/test_embed',
                                          channelId ? { channel_id: channelId } : {}),
      testDm:        (discordId)  => post('/api/discord/test_dm', { discord_id: discordId }),
      // v0.16.3 / task #165 — Full bot lifecycle smoke test
      mockVeto:      ()           => post('/api/discord/mock_veto', {}),
    },

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
    // v0.12.5 / task #95 — Gaming Mode toggle.  Wraps scripts/gaming-mode.ps1
    // (Power Plan / Game Mode / Game DVR / cs2.exe core affinity).  Local-only.
    gamingMode:    (mode) => post('/api/system/gaming_mode', { mode }),

    // ── Config backup / restore (v0.16.0 / task #158) ─────────────────────
    config_backup:        (reason = 'manual') => post('/api/config/backup', { reason }),
    config_backups:       ()                  => get('/api/config/backups'),
    config_restore:       (filename)          => post('/api/config/restore', { filename }),

    // ── Tournament readiness dashboard (v0.16.2 / task #168) ──────────────
    readiness:            ()                  => get('/api/readiness'),

    // ── Demo browser (v0.16.3 / task #171) ────────────────────────────────
    demos: {
      list:        ()      => get('/api/demos'),
      downloadUrl: (rel)   => `/api/demos/download?path=${encodeURIComponent(rel)}`,
    },

    // ── Persistent team profiles (v0.16.1 / task #160) ────────────────────
    teams: {
      list:   ()        => get('/api/teams'),
      save:   (team)    => post('/api/teams/save',   team),
      delete: (id)      => post('/api/teams/delete', { id }),
    },

    // ── Tournament templates (v0.16.3 / task #169) ────────────────────────
    templates: {
      list:   ()                 => get('/api/templates'),
      save:   (template)         => post('/api/templates/save',   template),
      delete: (id)               => post('/api/templates/delete', { id }),
      apply:  (id)               => post('/api/templates/apply',  { id }),
    },

    // ── Plugin Manager (v0.13.2 + v0.14.0) ────────────────────────────────
    plugins: {
      list:     ()              => get('/api/plugins'),
      // Activate a plugin by switching to a mode that uses it.  Pass mode=null
      // for single-mode plugins (backend picks); pass mode for multi-mode
      // plugins like MatchZy (Practice/3v3/4v4/5v5) or K4-Arenas (1v1/2v2).
      activate: (slug, mode = null) => post('/api/plugins/activate',
                                            mode ? { slug, mode } : { slug }),
      // Switch to vanilla Competitive — undeploys all managed plugins.
      vanilla:  ()              => post('/api/plugins/vanilla', {}),
      // v0.14.0: curated packs — one-click recipes (mode + map + plugins).
      packs:     ()             => get('/api/plugins/packs'),
      applyPack: (packId)       => post('/api/plugins/apply_pack', { pack_id: packId }),
      // v0.15.1 slice 2: community plugin registry.
      registry:        ()                    => get('/api/plugins/registry'),
      registryRefresh: ()                    => post('/api/plugins/registry/refresh', {}),
      installFromRegistry: (slug, version = null) =>
        post('/api/plugins/install_from_registry',
             version ? { slug, version } : { slug }),
      // v0.15.2 slice 3: uninstall + reload + custom URL install.
      uninstall:       (slug)                => post('/api/plugins/uninstall', { slug }),
      reload:          ()                    => post('/api/plugins/reload', {}),
      installFromUrl:  (url, sha256 = null, expectedSlug = null) => {
        const body = { url };
        if (sha256)       body.sha256        = sha256;
        if (expectedSlug) body.expected_slug = expectedSlug;
        return post('/api/plugins/install_from_url', body);
      },
    },

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

    // ── v0.11.4 — Diagnostic snapshot (text/plain, local-only) ────────────
    // Fetched as text not JSON; SPA copies straight to clipboard for
    // operator to paste into chat / Discord support.
    diagSnapshot: async () => {
      const r = await fetch('/api/diag/snapshot', { credentials: 'same-origin' });
      if (r.status === 401) { location.reload(); throw new Error('Session expired'); }
      if (r.status === 403) throw new Error('Diagnostic snapshot is local-only (admin on the host machine).');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.text();
    },

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
      // v0.12.3 / task #135 — per-player voting tokens.  voterTokens()
      // mints + auto-DMs all 10 (admin-only); voterClaim() consumes a
      // single token to mint a voter session cookie.
      voterTokens: ()                => post('/api/veto/voter_tokens', {}),
      voterClaim:  (token)           => post('/api/veto/voter_claim', { token }),
      step:       (team, mapId)      => post('/api/veto/step', { team, map_id: mapId }),
      finale:     (loadMatch=true)   => post('/api/veto/finale', { load_match: loadMatch }),
      reset:      ()                 => post('/api/veto/reset'),
      // v0.10.2 — last N completed matches persisted to oblivion_matches.json
      history:    ()                 => get('/api/veto/history'),
      // v0.11.0 polish — Spectator URL (read-only token-gated link).
      // issueSpectator() returns {token, urls, rotated}; pass {rotate:true}
      // to invalidate the previous link.
      issueSpectator: (rotate=false) => post('/api/veto/spectator', rotate ? {rotate:true} : {}),
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
