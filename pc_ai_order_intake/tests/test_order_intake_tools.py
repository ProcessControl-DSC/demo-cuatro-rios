# Part of Process Control. License LGPL-3.
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
