from odoo import models, fields, api
from odoo.exceptions import UserError


class ComplianceFleetWizard(models.TransientModel):
    _name = 'compliance.fleet.wizard'
    _description = 'Generate Vehicle Records from Fleet Compliance Data'

    fleet_count  = fields.Integer(string='Fleet Records Found',    readonly=True)
    reg_count    = fields.Integer(string='Unique Reg. Numbers',    readonly=True)
    exist_count  = fields.Integer(string='Vehicles Already Exist', readonly=True)
    new_count    = fields.Integer(string='Vehicles to Create',     readonly=True)
    state        = fields.Selection([('draft','Draft'),('done','Done')], default='draft')
    result_msg   = fields.Text(string='Result', readonly=True)

    def action_analyse(self):
        self.ensure_one()
        fleet_recs = self.env['compliance.record'].search([
            ('compliance_type', '=', 'fleet'),
            ('vehicle_reg', '!=', False),
        ])
        regs = {(r.vehicle_reg or '').strip().upper() for r in fleet_recs if r.vehicle_reg}
        existing = {
            v.vehicle_reg.upper()
            for v in self.env['compliance.vehicle'].search([])
        }
        self.write({
            'fleet_count': len(fleet_recs),
            'reg_count':   len(regs),
            'exist_count': len(regs & existing),
            'new_count':   len(regs - existing),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_generate(self):
        self.ensure_one()
        created = self.env['compliance.vehicle'].generate_from_fleet_records()
        self.write({
            'state':      'done',
            'result_msg': (
                f'{created} new vehicle record(s) created.\n'
                f'All fleet compliance records have been linked to their vehicles.\n\n'
                f'Go to Fleet → Vehicles to review.'
            ),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_fleet(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Fleet Vehicles',
            'res_model': 'compliance.vehicle',
            'view_mode': 'tree,form,kanban',
        }
