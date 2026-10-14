# Part of Process Control. License LGPL-3.
from unittest.mock import patch

from odoo.tests import tagged
from odoo.addons.base.tests.common import BaseCommon


@tagged('post_install', '-at_install')
class TestOrderIntakeTools(BaseCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.agent = cls.env.ref('pc_ai_order_intake.ai_agent_order_intake')
        cls.kg = cls.env.ref('uom.product_uom_kgm')
        cls.box = cls.env['uom.uom'].create({
            'name': 'Box 5 kg test', 'relative_uom_id': cls.kg.id, 'relative_factor': 5.0,
        })
        cls.chorizo = cls.env['product.product'].create({
            'name': 'Grill chorizo bulk', 'default_code': 'T110010', 'type': 'consu',
            'uom_id': cls.kg.id, 'list_price': 6.9, 'sale_ok': True,
            'description': '<p>Other names: parrillero, barbecue chorizo.</p>',
        })
        cls.burger = cls.env['product.product'].create({
            'name': 'Mixed burger 100 g', 'default_code': 'T130010', 'type': 'consu',
            'list_price': 0.62, 'sale_ok': True,
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Test Grill House', 'is_company': True, 'customer_rank': 1,
            'email': 'orders@testgrill.example.com', 'phone': '+34 600 999 111',
        })

    def _run_tool(self, xmlid, **arguments):
        tool = self.env.ref(xmlid)
        return tool._ai_tool_run(self.agent, arguments)

    def test_find_customer_by_email_and_phone(self):
        result = self._run_tool('pc_ai_order_intake.ai_tool_find_customer',
                                email='ORDERS@testgrill.example.com')
        self.assertEqual([c['partner_id'] for c in result['customers']], [self.customer.id])
        result = self._run_tool('pc_ai_order_intake.ai_tool_find_customer', phone='34600999111')
        self.assertEqual([c['partner_id'] for c in result['customers']], [self.customer.id])

    def test_find_products_by_alternative_name(self):
        """Customers order with their own words: the alias in the internal
        notes must find the product, plurals included."""
        result = self._run_tool('pc_ai_order_intake.ai_tool_find_products', text='parrilleros')
        self.assertIn(self.chorizo.id, [p['product_id'] for p in result['products']])
        kg_product = next(p for p in result['products'] if p['product_id'] == self.chorizo.id)
        self.assertIn('Box 5 kg test', kg_product['units'])

    def test_create_quotation_draft_with_units(self):
        result = self._run_tool(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id,
            product_ids=[self.chorizo.id, self.burger.id],
            quantities=[2, 40],
            uoms=['Box 5 kg test', ''],
            channel='whatsapp',
            customer_message='2 cajas de parrillero y 40 burger',
        )
        order = self.env['sale.order'].search([('name', '=', result['quotation'])])
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.partner_id, self.customer)
        self.assertEqual(order.order_line.mapped('product_uom_id'), self.box | self.burger.uom_id)
        self.assertIn(self.env.ref('pc_ai_order_intake.crm_tag_channel_whatsapp'), order.tag_ids)
        self.assertIn(order.get_portal_url(), result['portal_url'])

    def test_create_quotation_rejects_mismatched_lines(self):
        result = self._run_tool(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[],
            channel='email',
        )
        self.assertIn('error', result)

    def test_update_quotation_and_customer_confirmation(self):
        created = self._run_tool(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[10],
            channel='web',
        )
        result = self._run_tool(
            'pc_ai_order_intake.ai_tool_update_quotation',
            quotation=created['quotation'], partner_id=self.customer.id,
            product_ids=[self.chorizo.id, self.burger.id], quantities=[12, 20],
            customer_confirmed=True,
        )
        order = self.env['sale.order'].search([('name', '=', created['quotation'])])
        self.assertEqual(order.state, 'draft', "The AI never confirms: a person validates")
        self.assertEqual(
            sorted(order.order_line.mapped('product_uom_qty')), [12.0, 20.0])
        self.assertIn(self.env.ref('pc_ai_order_intake.crm_tag_customer_confirmed'), order.tag_ids)
        self.assertNotIn('error', result)

    def test_update_quotation_of_other_customer_refused(self):
        created = self._run_tool(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[10],
            channel='web',
        )
        intruder = self.env['res.partner'].create({'name': 'Other customer', 'customer_rank': 1})
        result = self._run_tool(
            'pc_ai_order_intake.ai_tool_update_quotation',
            quotation=created['quotation'], partner_id=intruder.id,
            product_ids=[self.chorizo.id], quantities=[1],
        )
        self.assertIn('error', result)

    def _run_tool_as_public(self, xmlid, **arguments):
        """The website chat runs the tools as the public user."""
        public = self.env.ref('base.public_user')
        tool = self.env.ref(xmlid).with_user(public).sudo()
        return tool._ai_tool_run(self.agent.with_user(public), arguments)

    def test_public_visitor_cannot_browse_customers(self):
        result = self._run_tool_as_public('pc_ai_order_intake.ai_tool_find_customer',
                                          name='Test Grill')
        self.assertEqual(result['customers'], [])
        result = self._run_tool_as_public('pc_ai_order_intake.ai_tool_find_customer',
                                          email='orders@testgrill.example.com')
        self.assertEqual(result['customers'], [], "Email alone is not enough on the website")
        result = self._run_tool_as_public('pc_ai_order_intake.ai_tool_find_customer',
                                          email='orders@testgrill.example.com', phone='600 999 111')
        self.assertEqual([c['partner_id'] for c in result['customers']], [self.customer.id])
        self.assertNotIn('pricelist', result['customers'][0])

    def test_public_visitor_needs_verified_email(self):
        result = self._run_tool_as_public('pc_ai_order_intake.ai_tool_customer_history',
                                          partner_id=self.customer.id)
        self.assertIn('error', result)
        result = self._run_tool_as_public(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[5],
            channel='web')
        self.assertIn('error', result)
        result = self._run_tool_as_public(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[5],
            channel='web', contact_email='orders@testgrill.example.com')
        self.assertNotIn('error', result)

    def test_public_visitor_verified_with_contact_email(self):
        """The visitor may give the email of a contact of the customer: the
        tools work on the company record and must accept it."""
        buyer = self.env['res.partner'].create({
            'name': 'Buyer of Test Grill', 'parent_id': self.customer.id,
            'email': 'buyer@testgrill.example.com', 'phone': '+34 600 999 222'})
        result = self._run_tool_as_public('pc_ai_order_intake.ai_tool_find_customer',
                                          email='buyer@testgrill.example.com', phone='600999222')
        self.assertEqual([c['partner_id'] for c in result['customers']], [self.customer.id])
        result = self._run_tool_as_public(
            'pc_ai_order_intake.ai_tool_customer_history',
            partner_id=self.customer.id, contact_email='buyer@testgrill.example.com')
        self.assertNotIn('error', result)
        self.assertTrue(buyer)

    def test_sender_lock_from_email_or_whatsapp(self):
        """A message processed by the crons may only touch its sender, whatever
        the text asks for (prompt injection in an email)."""
        other = self.env['res.partner'].create({
            'name': 'Test Grill Rival', 'is_company': True, 'customer_rank': 1})
        locked_agent = self.agent.with_context(ai_order_partner_id=self.customer.id)
        tool = self.env.ref('pc_ai_order_intake.ai_tool_find_customer').with_context(
            ai_order_partner_id=self.customer.id)
        result = tool._ai_tool_run(locked_agent, {'name': 'Test Grill Rival'})
        self.assertEqual([c['partner_id'] for c in result['customers']], [self.customer.id])
        tool = self.env.ref('pc_ai_order_intake.ai_tool_create_quotation').with_context(
            ai_order_partner_id=self.customer.id)
        result = tool._ai_tool_run(locked_agent, {
            'partner_id': other.id, 'product_ids': [self.chorizo.id], 'quantities': [1],
            'channel': 'email'})
        self.assertIn('error', result)

    def test_email_answer_formatted_with_quotation_link(self):
        """The email cron answers the customer's last email: the agent text
        in paragraphs, the quotation lines and totals, and a link to the
        quotation in the customer portal (view only, no online acceptance)."""
        team = self.env.ref('pc_ai_order_intake.crm_team_orders')
        lead = self.env['crm.lead'].create({
            'name': 'Pedido', 'team_id': team.id, 'partner_id': self.customer.id,
            'email_from': self.customer.email,
        })
        lead.message_post(body='Quiero 2 cajas de parrillero', message_type='email',
                          author_id=self.customer.id)
        created = self._run_tool(
            'pc_ai_order_intake.ai_tool_create_quotation',
            partner_id=self.customer.id, product_ids=[self.chorizo.id], quantities=[2],
            uoms=['Box 5 kg test'], channel='email', lead_id=lead.id)
        order = self.env['sale.order'].search([('name', '=', created['quotation'])])
        self.assertFalse(order.require_signature or order.require_payment)
        agent_class = type(self.env['ai.agent'])
        with patch.object(agent_class, 'get_direct_response',
                          return_value=['Hola, Juan:\nTe he preparado el pedido.\nUn saludo']):
            self.env.ref('pc_ai_order_intake.ir_cron_order_intake_email').ir_actions_server_id.run()
        def replies():
            return self.env['mail.message'].search([
                ('model', '=', 'crm.lead'), ('res_id', '=', lead.id),
                ('message_type', '=', 'comment'), ('partner_ids', '!=', False)])
        reply = replies()
        self.assertEqual(len(reply), 1)
        self.assertIn('<p', reply.body)
        self.assertIn('Te he preparado el pedido.', reply.body)
        self.assertIn(order.get_portal_url(), reply.body)
        self.assertIn('Grill chorizo bulk', reply.body)
        self.assertEqual(reply.author_id, self.env.company.partner_id)
        if team.alias_email:  # no alias domain on a bare test database
            self.assertIn(team.alias_email, reply.email_from)
        # a second run does not answer twice
        with patch.object(agent_class, 'get_direct_response', return_value=['Otra vez']):
            self.env.ref('pc_ai_order_intake.ir_cron_order_intake_email').ir_actions_server_id.run()
        self.assertEqual(len(replies()), 1)

    def test_clear_email_order_gets_no_answer(self):
        """When every product is matched the customer gets no message: the
        team validates the quotation. A note marks the email as handled, so
        the next run does not process it again."""
        team = self.env.ref('pc_ai_order_intake.crm_team_orders')
        lead = self.env['crm.lead'].create({
            'name': 'Pedido', 'team_id': team.id, 'partner_id': self.customer.id,
            'email_from': self.customer.email,
        })
        lead.message_post(body='10 kg de parrillero', message_type='email', author_id=self.customer.id)
        cron_action = self.env.ref('pc_ai_order_intake.ir_cron_order_intake_email').ir_actions_server_id
        agent_class = type(self.env['ai.agent'])
        with patch.object(agent_class, 'get_direct_response', return_value=['NO_REPLY']) as agent_call:
            cron_action.run()
            cron_action.run()
        self.assertEqual(agent_call.call_count, 1)
        comments = self.env['mail.message'].search([
            ('model', '=', 'crm.lead'), ('res_id', '=', lead.id), ('message_type', '=', 'comment')])
        self.assertEqual(len(comments), 1)
        self.assertFalse(comments.partner_ids, "Internal note only, nothing to the customer")

    def test_customer_history_last_order_and_suggestions(self):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'order_line': [(0, 0, {'product_id': self.chorizo.id, 'product_uom_qty': 8})],
        })
        order.action_confirm()
        result = self._run_tool('pc_ai_order_intake.ai_tool_customer_history',
                                partner_id=self.customer.id)
        self.assertEqual(result['last_order']['name'], order.name)
        self.assertEqual(result['last_order']['lines'][0]['product_id'], self.chorizo.id)
