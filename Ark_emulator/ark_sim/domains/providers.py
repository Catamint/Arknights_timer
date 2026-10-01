"""Implemented provider registry; game formulas live in a preset module."""
from ark_sim.presets import providers as ark

BUILTIN_PROVIDERS = {
    "ark.attributes.layers": ark.attribute_layers,
    "ark.attributes.aggregate": ark.aggregate_layer,
    "ark.resource.bounds": ark.resource_bounds,
    "ark.deploy.ground": ark.ground_deploy,
    "ark.lifecycle.standard": ark.lifecycle,
    "ark.lifecycle.result": ark.battle_result,
    "ark.targeting.score": ark.targeting_score,
    "ark.targeting.selection": ark.targeting_selection,
    "ark.behavior.player_combat": ark.player_behavior,
    "ark.behavior.ground_melee": ark.ground_behavior,
    "ark.selector.grid": ark.selector_grid,
    "ark.spatial.route": ark.spatial_route,
    "ark.spatial.blocking": ark.spatial_blocking,
    "ark.damage.pipeline": ark.damage_pipeline,
}

for _function in BUILTIN_PROVIDERS.values():
    _function.version = "ark-preset/1"
