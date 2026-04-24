from odoo import models, fields


class ComplianceDivision(models.Model):
    _name = 'compliance.division'
    _description = 'Compliance Division'
    _order = 'name'

    name = fields.Char(string='Division Name', required=True)
    code = fields.Char(string='Code')
    active = fields.Boolean(default=True)
    record_count = fields.Integer(compute='_compute_record_count', string='Compliances')

    def _compute_record_count(self):
        for rec in self:
            rec.record_count = self.env['compliance.record'].search_count(
                [('division_id', '=', rec.id)]
            )
