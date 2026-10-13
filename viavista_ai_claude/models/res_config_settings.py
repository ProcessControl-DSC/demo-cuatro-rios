from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    anthropic_key_enabled = fields.Boolean(
        string="Enable custom Anthropic API key",
        compute='_compute_anthropic_key_enabled',
        readonly=False,
        groups='base.group_system',
    )
    anthropic_key = fields.Char(
        string="Anthropic API key",
        config_parameter='ai.anthropic_key',
        readonly=False,
        groups='base.group_system',
    )

    def _compute_anthropic_key_enabled(self):
        for record in self:
            record.anthropic_key_enabled = bool(record.anthropic_key)
