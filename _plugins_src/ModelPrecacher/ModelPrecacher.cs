using CounterStrikeSharp.API.Core;

namespace ModelPrecacher;

// Precaches the non-default player models that WarcraftPlugin's Barbarian class
// assigns via SetModel.  They live in pak01.vpk but the engine only auto-
// precaches the DEFAULT team models, so without this the engine logs
// "RESOURCE_TYPE_MODEL … requested but is not in the system (Missing from a
// manifest?)" and Barbarian renders the error/default model.  Registering them
// in OnServerPrecacheResources adds them to the precache manifest so SetModel
// resolves them correctly.
public class ModelPrecacherPlugin : BasePlugin
{
    public override string ModuleName => "ModelPrecacher";
    public override string ModuleVersion => "1.0.0";
    public override string ModuleAuthor => "Oblivion Server Tool";
    public override string ModuleDescription =>
        "Precaches non-default player models used by WarcraftPlugin classes.";

    private static readonly string[] Models =
    {
        "characters/models/tm_phoenix_heavy/tm_phoenix_heavy.vmdl",
        "characters/models/ctm_heavy/ctm_heavy.vmdl",
    };

    public override void Load(bool hotReload)
    {
        RegisterListener<Listeners.OnServerPrecacheResources>(manifest =>
        {
            foreach (var model in Models)
                manifest.AddResource(model);
        });
    }
}
