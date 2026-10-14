# Part of Process Control. License LGPL-3.
import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged
from odoo.addons.base.tests.common import BaseCommon


@tagged('post_install', '-at_install')
class TestMessaging(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env['res.partner'].create({
            'name': 'Test Grill House', 'is_company': True, 'customer_rank': 1,
            'email': 'orders@testgrill.example.com', 'phone': '+34 600 999 111',
        })
        cls.Lead = cls.env['crm.lead'].sudo()
        cls.agent_class = type(cls.env['ai.agent'])

    def test_unknown_chat_asks_for_the_phone(self):
        with patch.object(self.agent_class, 'get_direct_response') as agent_call:
            result = self.Lead._messaging_receive('telegram', '1001', 'Juan', 'Hola, quiero pedir')
        self.assertTrue(result['need_contact'])
        agent_call.assert_not_called()
        lead = self.Lead.search([('messaging_ref', '=', 'telegram:1001')])
        self.assertEqual(lead.team_id, self.env.ref('pc_ai_order_intake.crm_team_orders'))
        self.assertIn(self.env.ref('pc_ai_order_intake.crm_tag_channel_telegram'), lead.tag_ids)

    def test_shared_phone_identifies_and_locks_the_customer(self):
        self.Lead._messaging_receive('telegram', '1002', 'Juan', 'Hola')
        with patch.object(self.agent_class, 'get_direct_response',
                          autospec=True, return_value=['Hola, Test Grill House']) as agent_call:
            result = self.Lead._messaging_receive('telegram', '1002', 'Juan', '', phone='34600999111')
        self.assertFalse(result.get('need_contact'))
        self.assertEqual(result['reply'], 'Hola, Test Grill House')
        agent = agent_call.call_args.args[0]
        self.assertEqual(agent.env.context['ai_order_partner_id'], self.customer.id)
        lead = self.Lead.search([('messaging_ref', '=', 'telegram:1002')])
        self.assertEqual(lead.partner_id.commercial_partner_id, self.customer)
        notes = self.env['mail.message'].search([('model', '=', 'crm.lead'), ('res_id', '=', lead.id)])
        self.assertFalse(notes.partner_ids, "The conversation is kept as internal notes, no email")
        self.assertIn('Hola, Test Grill House', ''.join(notes.mapped(lambda m: str(m.body))))


    def test_phone_channel_reads_aloud(self):
        with patch.object(self.agent_class, 'get_direct_response',
                          autospec=True, return_value=['Hecho']) as agent_call:
            self.Lead._messaging_receive('phone', '34600999111', '+34600999111',
                                         '10 kilos de parrillero', phone='+34600999111')
        prompt = agent_call.call_args.args[1]
        self.assertIn('read aloud', prompt)
        lead = self.Lead.search([('messaging_ref', '=', 'phone:34600999111')])
        self.assertIn(self.env.ref('pc_ai_order_intake.crm_tag_channel_phone'), lead.tag_ids)


@tagged('post_install', '-at_install')
class TestMessagingRoute(HttpCase):

    def test_route_requires_the_token(self):
        self.authenticate(None, None)  # public session bound to the test database
        url = '/pc_ai_order_intake/messaging'
        body = json.dumps({'channel': 'telegram', 'chat_id': '1', 'text': 'hola'})
        response = self.url_open(url, data=body, headers={'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 403)
        self.env['ir.config_parameter'].sudo().set_param('pc_ai_order_intake.messaging_token', 's3cret-test')
        response = self.url_open(url, data=body, headers={
            'Content-Type': 'application/json', 'X-Order-Intake-Token': 'wrong'})
        self.assertEqual(response.status_code, 403)
        response = self.url_open(url, data=body, headers={
            'Content-Type': 'application/json', 'X-Order-Intake-Token': 's3cret-test'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['need_contact'])
