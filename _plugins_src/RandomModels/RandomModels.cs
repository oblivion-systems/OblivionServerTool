using System.Drawing;
using System.Globalization;
using System.Text.Json.Serialization;
using CounterStrikeSharp.API;
using CounterStrikeSharp.API.Core;
using CounterStrikeSharp.API.Modules.Timers;
using CounterStrikeSharp.API.Modules.Utils;
using Microsoft.Extensions.Logging;

namespace RandomModels;

// ─────────────────────────────────────────────────────────────────────────────
//  RandomModels v2.5 — TIMER-DRIVEN CHAOS: random model + 1 of 23 abilities
//  per life.  No game events on the PR#1348 host, so everything runs off the
//  0.5s poll timer (assign/refresh/one-shot gives, kill-count polling for
//  Vampire, spotted-mask writes for Radar Hack), OnTick (movement powers),
//  and direct schema property writes.  Skins removed (crashed native);
//  pawn↔controller alignment guard (bot-takeover pawn swaps crashed the server).
// ─────────────────────────────────────────────────────────────────────────────

public class RandomModelsConfig : BasePluginConfig
{
    [JsonPropertyName("Models")]
    public List<string> Models { get; set; } = new()
    { "characters/models/ctm_sas/ctm_sas.vmdl", "characters/models/tm_phoenix/tm_phoenix.vmdl" };

    [JsonPropertyName("EnableModels")]    public bool EnableModels { get; set; } = true;
    [JsonPropertyName("EnableAbilities")] public bool EnableAbilities { get; set; } = true;
    [JsonPropertyName("IncludeBots")]     public bool IncludeBots { get; set; } = true;
    [JsonPropertyName("AnnounceInChat")]  public bool AnnounceInChat { get; set; } = true;
    [JsonPropertyName("TickSeconds")]     public float TickSeconds { get; set; } = 0.5f;
    [JsonPropertyName("BurstTicks")]      public int BurstTicks { get; set; } = 6;
    [JsonPropertyName("ReapplyEveryTicks")] public int ReapplyEveryTicks { get; set; } = 6;
}

public class RandomModelsPlugin : BasePlugin, IPluginConfig<RandomModelsConfig>
{
    public override string ModuleName => "RandomModels";
    public override string ModuleVersion => "2.6.2";
    public override string ModuleAuthor => "Oblivion Server Tool";
    public override string ModuleDescription => "Random model + ability each life (timer + OnTick).";

    public RandomModelsConfig Config { get; set; } = new();

    private readonly Random _rng = new();
    private readonly Dictionary<int, string> _model = new();
    private readonly Dictionary<int, Ability> _ability = new();
    private readonly Dictionary<int, Color> _neon = new();
    private readonly Dictionary<int, bool> _wasAlive = new();
    private readonly Dictionary<int, int> _lifeTicks = new();
    private readonly Dictionary<int, int> _lifeKills = new();   // Vampire: match-kills baseline
    private readonly HashSet<int> _moveSlots = new();
    private readonly HashSet<int> _kangarooUsed = new();        // per-airtime jump-boost latch
    private int _tick;

    private enum Ability
    {
        None, Tank, Moon, Ghost, Speed, Neon,
        Giant, Tiny, BottomlessMags, Moneybags, Juggernaut, GrenadeSanta,
        LootBox, Regenerator, Disco, Kangaroo, WideEye, TaserTime,
        Vampire, Adrenaline, BunnyHopper, Flicker, MedicKit, RadarHack, SixthSense,
    }

    // One entry each + a single None → ~5% of lives are "normal".
    // v2.6.2: Giant/Tiny (model scaling → client render crashes on complex
    // rigs), Flicker (RenderFX), and BottomlessMags/Vampire (native VData/
    // MatchStats reads → server AVs) removed after live crash reports.
    private static readonly Ability[] Roll =
    {
        Ability.None, Ability.Tank, Ability.Moon, Ability.Ghost, Ability.Speed, Ability.Neon,
        Ability.Moneybags, Ability.Juggernaut,
        Ability.GrenadeSanta, Ability.LootBox, Ability.Regenerator, Ability.Disco,
        Ability.Kangaroo, Ability.WideEye, Ability.TaserTime,
        Ability.Adrenaline, Ability.BunnyHopper,
        Ability.MedicKit, Ability.RadarHack, Ability.SixthSense,
    };

    private static string Label(Ability a) => a switch
    {
        Ability.Tank => "\x06Tank (200 HP)",
        Ability.Moon => "\x0BMoon Jump",
        Ability.Ghost => "\x08Ghost",
        Ability.Speed => "\x04Speedster",
        Ability.Neon => "\x0ENeon Glow",
        Ability.Giant => "\x02GIANT",
        Ability.Tiny => "\x0BTiny",
        Ability.BottomlessMags => "\x10Bottomless Mags",
        Ability.Moneybags => "\x04Moneybags ($16k)",
        Ability.Juggernaut => "\x08Juggernaut (armor)",
        Ability.GrenadeSanta => "\x02Grenade Santa",
        Ability.LootBox => "\x0ELoot Box (free gun)",
        Ability.Regenerator => "\x04Regenerator",
        Ability.Disco => "\x0EDisco",
        Ability.Kangaroo => "\x0BKangaroo (high jump)",
        Ability.WideEye => "\x09Wide-Eye (110 FOV)",
        Ability.TaserTime => "\x0BZeus Included",
        Ability.Vampire => "\x02Vampire (kills heal +50)",
        Ability.Adrenaline => "\x04Adrenaline (fast when hurt)",
        Ability.BunnyHopper => "\x0BBunny Hopper (hold jump)",
        Ability.Flicker => "\x08Flicker (strobe)",
        Ability.MedicKit => "\x04Medic Kit (2 healthshots)",
        Ability.RadarHack => "\x09Radar Hack (enemies pinged)",
        Ability.SixthSense => "\x09Sixth Sense (enemy tracker)",
        _ => "\x01normal",
    };

    private static readonly string[] LootGuns =
    { "weapon_awp", "weapon_negev", "weapon_deagle", "weapon_p90", "weapon_mag7",
      "weapon_ssg08", "weapon_m249" };   // dropped weapon_shield (unusual entity, crash risk)

    public void OnConfigParsed(RandomModelsConfig config) => Config = config;

    public override void Load(bool hotReload)
    {
        RegisterListener<Listeners.OnServerPrecacheResources>(manifest =>
        {
            foreach (var model in Config.Models) manifest.AddResource(model);
            Logger.LogInformation("[RM] Precache listener fired — {C} model(s).", Config.Models.Count);
        });
        AddTimer(Config.TickSeconds, Tick, TimerFlags.REPEAT);
        RegisterListener<Listeners.OnTick>(OnEngineTick);
        Logger.LogInformation("[RM] v2.6.2 loaded (hotReload={H}) — models={M} abilities={A} ({R} rolls); {C} model(s).",
            hotReload, Config.EnableModels, Config.EnableAbilities, Roll.Length, Config.Models.Count);
    }

    // Pawn must still belong to this controller — during bot takeover the pawn
    // swaps owners mid-frame and touching it then crashes the server (native AV).
    private static CCSPlayerPawn? AlignedPawn(CCSPlayerController p)
    {
        var pawn = p.PlayerPawn.Value;
        if (pawn is null || !pawn.IsValid) return null;
        var ctl = pawn.Controller.Value;
        if (ctl is null || ctl.Index != p.Index) return null;
        return pawn;
    }

    private void Tick()
    {
        _tick++;
        int alive = 0, applied = 0;
        _moveSlots.Clear();

        List<CCSPlayerController> players;
        try { players = Utilities.GetPlayers(); }
        catch (Exception ex) { if (_tick % 20 == 1) Logger.LogError(ex, "[RM] Tick GetPlayers threw."); return; }

        foreach (var p in players)
        {
            if (!IsEligible(p)) continue;
            if (!p.PawnIsAlive) { _wasAlive[p.Slot] = false; continue; }
            alive++;

            bool first = !_wasAlive.GetValueOrDefault(p.Slot);
            if (first)
            {
                _model[p.Slot] = RandomModel();
                _ability[p.Slot] = Config.EnableAbilities ? Roll[_rng.Next(Roll.Length)] : Ability.None;
                _neon[p.Slot] = NeonColor();
                _lifeTicks[p.Slot] = 0;
                _kangarooUsed.Remove(p.Slot);
                _lifeKills[p.Slot] = MatchKills(p);
                // model is logged every spawn so a client crash can be traced to the
                // exact model: cross-reference the crash time against this line.
                Logger.LogInformation("[RM] {N} spawned -> model={M} ability={A}",
                    SafeName(p), ModelName(_model[p.Slot]), _ability[p.Slot]);
            }
            _wasAlive[p.Slot] = true;
            int t = ++_lifeTicks[p.Slot];

            var ab = _ability.GetValueOrDefault(p.Slot);
            if (ab is Ability.Moon or Ability.Speed or Ability.Kangaroo
                   or Ability.Adrenaline or Ability.BunnyHopper) _moveSlots.Add(p.Slot);
            if (first && Config.AnnounceInChat && !p.IsBot) Announce(p);

            var pawn = AlignedPawn(p);
            if (pawn is null) continue;

            if (first && Config.EnableAbilities)
            {
                Normalize(p, pawn);                 // clear leftovers from the previous life
                ApplyOneShot(p, pawn, ab);          // gives: nades, gun, money, taser, healthshots
            }

            // Every-tick abilities (0.5s cadence): regen, clip top-up, disco, vampire, radar.
            if (Config.EnableAbilities)
            {
                ApplyEveryTick(p, pawn, ab);
                if (ab == Ability.RadarHack) RadarPing(p, players);
                if (ab == Ability.SixthSense && !p.IsBot && t % 2 == 0) SixthSense(p, pawn, players);
            }

            bool burst = t <= Config.BurstTicks;
            bool doApply = burst || (t % Math.Max(1, Config.ReapplyEveryTicks) == 0);
            if (!doApply) continue;

            if (Config.EnableModels)
            {
                try { pawn.SetModel(_model[p.Slot]); applied++; }
                catch (Exception ex) { Logger.LogError(ex, "[RM] SetModel threw {N}", SafeName(p)); }
            }
            if (Config.EnableAbilities) ApplyStatic(pawn, ab, p.Slot, burst);
        }

        if (_tick <= 4 || _tick % 40 == 0)
            Logger.LogInformation("[RM] Tick#{T}: {A} alive, applied {P}, move {M}.", _tick, alive, applied, _moveSlots.Count);
    }

    private static int MatchKills(CCSPlayerController p)
    {
        try { return p.ActionTrackingServices?.MatchStats.Kills ?? 0; }
        catch { return 0; }
    }

    /// Reset per-pawn state a previous life's ability may have left behind
    /// (warmup respawns can reuse the pawn entity).
    private void Normalize(CCSPlayerController p, CCSPlayerPawn pawn)
    {
        try
        {
            pawn.RenderMode = RenderMode_t.kRenderNormal;
            pawn.RenderFX = RenderFx_t.kRenderFxNone;
            pawn.Render = Color.FromArgb(255, 255, 255, 255);
            Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_nRenderMode");
            Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_nRenderFX");
            Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_clrRender");
            var node = pawn.CBodyComponent?.SceneNode;
            if (node is not null && Math.Abs(node.Scale - 1f) > 0.01f)
            {
                node.Scale = 1f;
                Utilities.SetStateChanged(pawn, "CBaseEntity", "m_CBodyComponent");
            }
            if (p.DesiredFOV != 90)
            {
                p.DesiredFOV = 90;
                Utilities.SetStateChanged(p, "CBasePlayerController", "m_iDesiredFOV");
            }
        }
        catch (Exception ex) { if (_tick % 40 == 1) Logger.LogError(ex, "[RM] Normalize threw."); }
    }

    /// One-time grants on the first tick of a life.
    private void ApplyOneShot(CCSPlayerController p, CCSPlayerPawn pawn, Ability ability)
    {
        try
        {
            switch (ability)
            {
                case Ability.Moneybags:
                    var money = p.InGameMoneyServices;
                    if (money is not null)
                    {
                        money.Account = 16000;
                        Utilities.SetStateChanged(p, "CCSPlayerController", "m_pInGameMoneyServices");
                    }
                    break;
                case Ability.GrenadeSanta:
                    p.GiveNamedItem("weapon_hegrenade");
                    p.GiveNamedItem("weapon_flashbang");
                    p.GiveNamedItem("weapon_smokegrenade");
                    p.GiveNamedItem(p.Team == CsTeam.Terrorist ? "weapon_molotov" : "weapon_incgrenade");
                    break;
                case Ability.LootBox:
                    p.GiveNamedItem(LootGuns[_rng.Next(LootGuns.Length)]);
                    break;
                case Ability.TaserTime:
                    p.GiveNamedItem("weapon_taser");
                    break;
                case Ability.MedicKit:
                    p.GiveNamedItem("weapon_healthshot");
                    p.GiveNamedItem("weapon_healthshot");
                    break;
                case Ability.WideEye:
                    p.DesiredFOV = 110;
                    Utilities.SetStateChanged(p, "CBasePlayerController", "m_iDesiredFOV");
                    break;
            }
        }
        catch (Exception ex) { Logger.LogError(ex, "[RM] ApplyOneShot({Ab}) threw.", ability); }
    }

    /// Runs every poll tick (0.5s) for the whole life.
    private void ApplyEveryTick(CCSPlayerController p, CCSPlayerPawn pawn, Ability ability)
    {
        try
        {
            switch (ability)
            {
                case Ability.Regenerator:
                    if (pawn.Health > 0 && pawn.Health < 100)
                    {
                        pawn.Health = Math.Min(pawn.Health + 2, 100);   // ~4 HP/s
                        Utilities.SetStateChanged(pawn, "CBaseEntity", "m_iHealth");
                    }
                    break;
                case Ability.BottomlessMags:
                    var ws = pawn.WeaponServices;
                    var active = ws?.ActiveWeapon.Value;
                    if (active is not null && active.IsValid)
                    {
                        int max = (active.VData as CCSWeaponBaseVData)?.MaxClip1 ?? 30;
                        if (active.Clip1 < max)
                        {
                            active.Clip1 = max;
                            Utilities.SetStateChanged(active, "CBasePlayerWeapon", "m_iClip1");
                        }
                    }
                    break;
                case Ability.Disco:
                    var hue = (_tick * 25) % 360;
                    pawn.Render = FromHsv(hue);
                    Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_clrRender");
                    break;
                case Ability.Vampire:
                    int kills = MatchKills(p);
                    int baseline = _lifeKills.GetValueOrDefault(p.Slot, kills);
                    if (kills > baseline)
                    {
                        int heal = (kills - baseline) * 50;
                        pawn.Health = Math.Min(pawn.Health + heal, 175);
                        Utilities.SetStateChanged(pawn, "CBaseEntity", "m_iHealth");
                        _lifeKills[p.Slot] = kills;
                    }
                    break;
            }
        }
        catch (Exception ex) { if (_tick % 40 == 1) Logger.LogError(ex, "[RM] ApplyEveryTick({Ab}) threw.", ability); }
    }

    /// Sixth Sense: HUD compass to the nearest living enemy (bearing + distance).
    private void SixthSense(CCSPlayerController me, CCSPlayerPawn myPawn, List<CCSPlayerController> players)
    {
        try
        {
            var origin = myPawn.AbsOrigin;
            if (origin is null) return;
            float best = float.MaxValue;
            CCSPlayerController? near = null;
            Vector? nearPos = null;
            foreach (var o in players)
            {
                if (o.Slot == me.Slot || !o.IsValid || !o.PawnIsAlive) continue;
                if (o.Team == me.Team ||
                    (o.Team != CsTeam.Terrorist && o.Team != CsTeam.CounterTerrorist)) continue;
                var op = o.PlayerPawn.Value?.AbsOrigin;
                if (op is null) continue;
                float dxo = op.X - origin.X, dyo = op.Y - origin.Y, dzo = op.Z - origin.Z;
                float d = MathF.Sqrt(dxo * dxo + dyo * dyo + dzo * dzo);
                if (d < best) { best = d; near = o; nearPos = op; }
            }
            if (near is null || nearPos is null) return;

            float bearing = MathF.Atan2(nearPos.Y - origin.Y, nearPos.X - origin.X) * 180f / MathF.PI;
            float rel = bearing - myPawn.EyeAngles.Y;
            while (rel > 180f) rel -= 360f;
            while (rel < -180f) rel += 360f;
            // Positive rel = enemy to the LEFT of view (Source yaw grows CCW).
            string arrow = rel switch
            {
                >= -22.5f and <= 22.5f => "↑",   // ahead
                > 22.5f and <= 67.5f => "↖",      // front-left
                > 67.5f and <= 112.5f => "←",     // left
                > 112.5f and <= 157.5f => "↙",    // back-left
                < -22.5f and >= -67.5f => "↗",    // front-right
                < -67.5f and >= -112.5f => "→",   // right
                < -112.5f and >= -157.5f => "↘",  // back-right
                _ => "↓",                          // behind
            };
            int meters = (int)MathF.Round(best / 52.5f);
            me.PrintToCenter($"◉ 6th Sense  —  nearest enemy {meters}m  {arrow}");
        }
        catch (Exception ex) { if (_tick % 40 == 1) Logger.LogError(ex, "[RM] SixthSense threw."); }
    }

    /// Radar Hack: mark every living enemy as spotted by this player (0.5s cadence).
    private void RadarPing(CCSPlayerController viewer, List<CCSPlayerController> players)
    {
        try
        {
            foreach (var other in players)
            {
                if (other.Slot == viewer.Slot || !other.IsValid || !other.PawnIsAlive) continue;
                if (other.Team == viewer.Team ||
                    (other.Team != CsTeam.Terrorist && other.Team != CsTeam.CounterTerrorist)) continue;
                var ep = other.PlayerPawn.Value;
                if (ep is null || !ep.IsValid) continue;
                var spotted = ep.EntitySpottedState;
                spotted.Spotted = true;
                spotted.SpottedByMask[viewer.Slot / 32] |= 1u << (viewer.Slot % 32);
                Utilities.SetStateChanged(ep, "CCSPlayerPawn", "m_entitySpottedState");
            }
        }
        catch (Exception ex) { if (_tick % 40 == 1) Logger.LogError(ex, "[RM] RadarPing threw."); }
    }

    /// Burst + periodic re-applies (survive the engine's spawn-time resets).
    private void ApplyStatic(CCSPlayerPawn pawn, Ability ability, int slot, bool burst)
    {
        try
        {
            switch (ability)
            {
                case Ability.Tank:
                    if (burst)   // set during the burst to beat spawn reset, then stop (no permanent heal)
                    {
                        pawn.Health = 200; pawn.MaxHealth = 200;
                        Utilities.SetStateChanged(pawn, "CBaseEntity", "m_iHealth");
                        Utilities.SetStateChanged(pawn, "CBaseEntity", "m_iMaxHealth");
                    }
                    break;
                case Ability.Juggernaut:
                    if (burst)
                    {
                        pawn.ArmorValue = 100;
                        Utilities.SetStateChanged(pawn, "CCSPlayerPawn", "m_ArmorValue");
                    }
                    break;
                case Ability.Ghost:
                    // TransAlpha render mode makes the alpha actually blend on
                    // models whose materials don't declare translucency themselves.
                    pawn.RenderMode = RenderMode_t.kRenderTransAlpha;
                    pawn.Render = Color.FromArgb(110, 255, 255, 255);
                    Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_nRenderMode");
                    Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_clrRender");
                    break;
                case Ability.Neon:
                    pawn.Render = _neon.GetValueOrDefault(slot, Color.Magenta);   // stable per-life color, re-applied
                    Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_clrRender");
                    break;
                case Ability.Flicker:
                    pawn.RenderFX = RenderFx_t.kRenderFxStrobeFast;   // engine-animated strobe
                    Utilities.SetStateChanged(pawn, "CBaseModelEntity", "m_nRenderFX");
                    break;
                case Ability.Giant:
                case Ability.Tiny:
                    var node = pawn.CBodyComponent?.SceneNode;
                    if (node is not null)
                    {
                        float target = ability == Ability.Giant ? 1.35f : 0.62f;
                        if (Math.Abs(node.Scale - target) > 0.01f)
                        {
                            node.Scale = target;
                            Utilities.SetStateChanged(pawn, "CBaseEntity", "m_CBodyComponent");
                        }
                    }
                    break;
            }
        }
        catch (Exception ex) { Logger.LogError(ex, "[RM] ApplyStatic({Ab}) threw.", ability); }
    }

    private void OnEngineTick()
    {
        if (_moveSlots.Count == 0) return;
        foreach (var slot in _moveSlots)
        {
            var p = Utilities.GetPlayerFromSlot(slot);
            if (p is null || !p.IsValid || !p.PawnIsAlive) continue;
            var pawn = AlignedPawn(p);
            if (pawn is null) continue;
            var ab = _ability.GetValueOrDefault(slot);
            try
            {
                var v = pawn.AbsVelocity;
                bool onGround = (pawn.Flags & (uint)PlayerFlags.FL_ONGROUND) != 0;
                const PlayerButtons MoveKeys = PlayerButtons.Forward | PlayerButtons.Back
                                             | PlayerButtons.Moveleft | PlayerButtons.Moveright;
                switch (ab)
                {
                    case Ability.Moon:
                        // Airborne only — dampening ground descents made stairs/ramps floaty.
                        if (!onGround && v.Z < 0f) v.Z *= 0.55f;
                        break;
                    case Ability.Speed:
                        // Boost only while move keys are held — amplifying passive drift
                        // made stopping feel like ice (deceleration is still speed > 5).
                        if ((p.Buttons & MoveKeys) != 0)
                        {
                            float hs = MathF.Sqrt(v.X * v.X + v.Y * v.Y);
                            if (hs > 5f && hs < 340f) { v.X *= 1.12f; v.Y *= 1.12f; }
                        }
                        break;
                    case Ability.Adrenaline:
                        // Speed boost only while hurt — reward aggression under fire.
                        if (pawn.Health > 0 && pawn.Health < 35 && (p.Buttons & MoveKeys) != 0)
                        {
                            float ahs = MathF.Sqrt(v.X * v.X + v.Y * v.Y);
                            if (ahs > 5f && ahs < 360f) { v.X *= 1.15f; v.Y *= 1.15f; }
                        }
                        break;
                    case Ability.Kangaroo:
                        // Boost the first rising frame of each jump, once per airtime.
                        if (onGround) _kangarooUsed.Remove(slot);
                        else if (!_kangarooUsed.Contains(slot) && v.Z > 150f)
                        {
                            v.Z = MathF.Min(v.Z * 1.35f, 460f);
                            _kangarooUsed.Add(slot);
                        }
                        break;
                    case Ability.BunnyHopper:
                        // Hold jump = auto-bounce off the ground, no timing needed.
                        if (onGround && (p.Buttons & PlayerButtons.Jump) != 0)
                            v.Z = 300f;
                        break;
                }
            }
            catch { }
        }
    }

    private Color NeonColor()
    {
        Color[] n = { Color.FromArgb(255,0,255), Color.FromArgb(0,255,255), Color.FromArgb(0,255,0),
                      Color.FromArgb(255,255,0), Color.FromArgb(255,64,0), Color.FromArgb(128,0,255) };
        return n[_rng.Next(n.Length)];
    }

    private static Color FromHsv(int hue)
    {
        int x = (int)(255 * (1 - Math.Abs(hue / 60.0 % 2 - 1)));
        return (hue / 60) switch
        {
            0 => Color.FromArgb(255, x, 0), 1 => Color.FromArgb(x, 255, 0), 2 => Color.FromArgb(0, 255, x),
            3 => Color.FromArgb(0, x, 255), 4 => Color.FromArgb(x, 0, 255), _ => Color.FromArgb(255, 0, x),
        };
    }

    // Short model id for crash-tracing logs (e.g. "subway_jake_player_model").
    private static string ModelName(string path)
        => path.Split('/')[^1].Replace(".vmdl", "");

    private static string PrettyName(string modelPath)
    {
        var n = modelPath.Split('/')[^1];
        foreach (var suffix in new[] { ".vmdl", "_player_model", "_playermodel", "_sk2model", "_ag2" })
            n = n.Replace(suffix, "");
        return CultureInfo.InvariantCulture.TextInfo.ToTitleCase(n.Replace('_', ' '));
    }

    private void Announce(CCSPlayerController p)
    {
        try
        {
            var m = PrettyName(_model.GetValueOrDefault(p.Slot, ""));
            var parts = new List<string>();
            if (Config.EnableModels && !string.IsNullOrWhiteSpace(m)) parts.Add($"\x09{m}\x01");
            if (Config.EnableAbilities) parts.Add(Label(_ability.GetValueOrDefault(p.Slot)) + "\x01");
            if (parts.Count > 0) p.PrintToChat($" \x0E[Chaos]\x01 You are: {string.Join(" \x01+ ", parts)}");
        }
        catch { }
    }

    private static string SafeName(CCSPlayerController player)
    { try { return player.PlayerName ?? $"slot{player.Slot}"; } catch { return $"slot{player.Slot}"; } }

    private string RandomModel() => Config.Models[_rng.Next(Config.Models.Count)];

    private bool IsEligible(CCSPlayerController? player)
    {
        if (player is null || !player.IsValid) return false;
        if (player.IsHLTV) return false;
        if (player.IsBot && !Config.IncludeBots) return false;
        return player.Team == CsTeam.Terrorist || player.Team == CsTeam.CounterTerrorist;
    }
}
