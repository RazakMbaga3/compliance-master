from odoo import models, fields, api
from datetime import date
import logging

_logger = logging.getLogger(__name__)

# Agencies whose compliance records map to specific columns on the vehicle row
INSURANCE_KEYS  = ('alliance insurance', 'jubilee insurance', 'insurance')
ROAD_LIC_KEYS   = ('land transport regulatory', 'ltra', 'road licence', 'road license')
TRA_KEYS        = ('tanzania revenue authority', 'tra')
WEIGHTS_KEYS    = ('weights & measure', 'weights and measure', 'wma')

# State priority for rolling up multiple records into one overall state
STATE_PRIORITY = {'overdue': 0, 'due': 1, 'under_renewal': 2, 'active': 3, 'inactive': 4}

VEHICLE_TYPE_SEL = [
    ('bulk_tanker',  'Bulk Tanker'),
    ('truck',        'Truck / Lorry'),
    ('tipper',       'Tipper'),
    ('pickup',       'Pickup'),
    ('crane',        'Crane / Mobile Crane'),
    ('forklift',     'Forklift / Hydra'),
    ('bus',          'Bus / Coaster'),
    ('car',          'Car / SUV'),
    ('trailer',      'Trailer'),
    ('other',        'Other'),
]


def _worst_state(states):
    """Return the state with the highest urgency from a list of state strings."""
    filtered = [s for s in states if s and s != 'inactive']
    if not filtered:
        return 'inactive'
    return min(filtered, key=lambda s: STATE_PRIORITY.get(s, 99))


class ComplianceVehicle(models.Model):
    _name = 'compliance.vehicle'
    _description = 'Fleet Vehicle – Compliance Overview'
    _inherit = ['mail.thread']
    _order = 'vehicle_reg'
    _rec_name = 'vehicle_reg'

    # ── Identity ──────────────────────────────────────────────────────────────
    vehicle_reg  = fields.Char(string='Registration No.', required=True, tracking=True)
    vehicle_type = fields.Selection(VEHICLE_TYPE_SEL, string='Vehicle Type', tracking=True)
    make_model   = fields.Char(string='Make / Model', tracking=True)
    division_id  = fields.Many2one('compliance.division', string='Division', tracking=True)
    active       = fields.Boolean(default=True)
    notes        = fields.Text(string='Notes')

    # ── Linked compliance records ─────────────────────────────────────────────
    compliance_ids = fields.One2many(
        'compliance.record', 'vehicle_id',
        string='Compliance Records',
    )
    compliance_count = fields.Integer(
        compute='_compute_compliance_count', string='Compliances',
    )

    # ── Per-agency expiry & state (computed) ──────────────────────────────────
    insurance_expiry  = fields.Date(compute='_compute_agency_fields', store=True, string='Insurance Expiry')
    insurance_state   = fields.Selection(
        [('active','Active'),('due','Due'),('overdue','Overdue'),('under_renewal','Under Renewal'),('inactive','Inactive')],
        compute='_compute_agency_fields', store=True, string='Insurance Status',
    )
    road_lic_expiry   = fields.Date(compute='_compute_agency_fields', store=True, string='Road Licence Expiry')
    road_lic_state    = fields.Selection(
        [('active','Active'),('due','Due'),('overdue','Overdue'),('under_renewal','Under Renewal'),('inactive','Inactive')],
        compute='_compute_agency_fields', store=True, string='Road Licence Status',
    )
    tra_expiry        = fields.Date(compute='_compute_agency_fields', store=True, string='TRA Sticker Expiry')
    tra_state         = fields.Selection(
        [('active','Active'),('due','Due'),('overdue','Overdue'),('under_renewal','Under Renewal'),('inactive','Inactive')],
        compute='_compute_agency_fields', store=True, string='TRA Status',
    )
    weights_expiry    = fields.Date(compute='_compute_agency_fields', store=True, string='Weights Cert Expiry')
    weights_state     = fields.Selection(
        [('active','Active'),('due','Due'),('overdue','Overdue'),('under_renewal','Under Renewal'),('inactive','Inactive')],
        compute='_compute_agency_fields', store=True, string='Weights Status',
    )

    # ── Overall / worst state ─────────────────────────────────────────────────
    overall_state = fields.Selection([
        ('active',        'Fully Compliant'),
        ('due',           'Due for Renewal'),
        ('under_renewal', 'Under Renewal'),
        ('overdue',       'Non-Compliant'),
        ('inactive',      'Inactive'),
    ], compute='_compute_overall_state', store=True,
       string='Overall Status', tracking=True)

    days_to_nearest_expiry = fields.Integer(
        compute='_compute_overall_state', store=True,
        string='Days to Next Expiry',
    )

    # ══════════════════════════════════════════════════════════════════════════

    @api.depends('compliance_ids')
    def _compute_compliance_count(self):
        for v in self:
            v.compliance_count = len(v.compliance_ids)

    @api.depends(
        'compliance_ids.state', 'compliance_ids.expiry_date',
        'compliance_ids.agency',
    )
    def _compute_agency_fields(self):
        for v in self:
            ins = road = tra = wgt = None
            for rec in v.compliance_ids:
                al = (rec.agency or '').lower()
                if any(k in al for k in INSURANCE_KEYS):
                    ins = rec
                elif any(k in al for k in ROAD_LIC_KEYS):
                    road = rec
                elif any(k in al for k in TRA_KEYS):
                    tra = rec
                elif any(k in al for k in WEIGHTS_KEYS):
                    wgt = rec

            v.insurance_expiry = ins.expiry_date  if ins  else False
            v.insurance_state  = ins.state        if ins  else False
            v.road_lic_expiry  = road.expiry_date if road else False
            v.road_lic_state   = road.state       if road else False
            v.tra_expiry       = tra.expiry_date  if tra  else False
            v.tra_state        = tra.state        if tra  else False
            v.weights_expiry   = wgt.expiry_date  if wgt  else False
            v.weights_state    = wgt.state        if wgt  else False

    @api.depends('insurance_state', 'road_lic_state', 'tra_state', 'weights_state',
                 'insurance_expiry', 'road_lic_expiry', 'tra_expiry', 'weights_expiry')
    def _compute_overall_state(self):
        today = date.today()
        for v in self:
            states = [
                v.insurance_state, v.road_lic_state,
                v.tra_state, v.weights_state,
            ]
            v.overall_state = _worst_state(states)

            expiries = [
                d for d in [
                    v.insurance_expiry, v.road_lic_expiry,
                    v.tra_expiry, v.weights_expiry,
                ] if d
            ]
            if expiries:
                nearest = min(expiries)
                v.days_to_nearest_expiry = (nearest - today).days
            else:
                v.days_to_nearest_expiry = 0

    # ── Smart button ──────────────────────────────────────────────────────────
    def action_view_compliances(self):
        return {
            'type': 'ir.actions.act_window',
            'name': f'Compliances – {self.vehicle_reg}',
            'res_model': 'compliance.record',
            'view_mode': 'tree,form',
            'domain': [('vehicle_id', '=', self.id)],
            'context': {
                'default_vehicle_id': self.id,
                'default_compliance_type': 'fleet',
                'default_vehicle_reg': self.vehicle_reg,
            },
        }

    # ── Generate vehicles from existing fleet compliance records ─────────────
    @api.model
    def generate_from_fleet_records(self):
        """
        Called from the wizard or manually: scan all compliance.record rows
        where compliance_type='fleet' and vehicle_reg is set, then create
        compliance.vehicle records for any reg numbers not yet present.
        Returns a count of vehicles created.
        """
        fleet_records = self.env['compliance.record'].search([
            ('compliance_type', '=', 'fleet'),
            ('vehicle_reg', '!=', False),
        ])

        existing_regs = {
            v.vehicle_reg.upper()
            for v in self.search([])
        }

        grouped = {}
        for rec in fleet_records:
            reg = (rec.vehicle_reg or '').strip().upper()
            if not reg:
                continue
            if reg not in grouped:
                grouped[reg] = rec  # keep first record as source of division

        created = 0
        for reg, sample in grouped.items():
            if reg in existing_regs:
                # Just link the compliance records
                vehicle = self.search([('vehicle_reg', '=', reg)], limit=1)
            else:
                vehicle = self.create({
                    'vehicle_reg': reg,
                    'division_id': sample.division_id.id if sample.division_id else False,
                })
                existing_regs.add(reg)
                created += 1

            # Link all compliance.record rows for this reg to the vehicle
            recs_for_reg = self.env['compliance.record'].search([
                ('compliance_type', '=', 'fleet'),
                ('vehicle_reg', 'ilike', reg),
            ])
            recs_for_reg.write({'vehicle_id': vehicle.id})

        _logger.info('generate_from_fleet_records: created %d vehicles', created)
        return created
