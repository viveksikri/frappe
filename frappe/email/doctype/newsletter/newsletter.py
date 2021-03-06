# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals

import frappe
import frappe.utils
from frappe import throw, _
from frappe.model.document import Document
from frappe.email.queue import check_email_limit
from frappe.utils.verified_command import get_signed_params, verify_request
from frappe.utils.background_jobs import enqueue
from frappe.utils.scheduler import log
from frappe.email.queue import send
from frappe.email.doctype.email_group.email_group import add_subscribers

class Newsletter(Document):
	def autoname(self):
		self.name = self.subject

	def onload(self):
		if self.email_sent:
			self.get("__onload").status_count = dict(frappe.db.sql("""select status, count(name)
				from `tabEmail Queue` where reference_doctype=%s and reference_name=%s
				group by status""", (self.doctype, self.name))) or None

	def test_send(self, doctype="Lead"):
		self.recipients = frappe.utils.split_emails(self.test_email_id)
		self.queue_all()
		frappe.msgprint(_("Scheduled to send to {0}").format(self.test_email_id))

	def send_emails(self):
		"""send emails to leads and customers"""
		if self.email_sent:
			throw(_("Newsletter has already been sent"))

		self.recipients = self.get_recipients()

		if getattr(frappe.local, "is_ajax", False):
			self.validate_send()

			# using default queue with a longer timeout as this isn't a scheduled task
			enqueue(send_newsletter, queue='default', timeout=3000, event='send_newsletter', newsletter=self.name)

		else:
			self.queue_all()

		frappe.msgprint(_("Scheduled to send to {0} recipients").format(len(self.recipients)))

		frappe.db.set(self, "email_sent", 1)

	def queue_all(self):
		if not self.get("recipients"):
			# in case it is called via worker
			self.recipients = self.get_recipients()

		self.validate_send()

		sender = self.send_from or frappe.utils.get_formatted_email(self.owner)

		if not frappe.flags.in_test:
			frappe.db.auto_commit_on_many_writes = True

		send(recipients = self.recipients, sender = sender,
			subject = self.subject, message = self.message,
			reference_doctype = self.doctype, reference_name = self.name,
			unsubscribe_method = "/api/method/frappe.email.doctype.newsletter.newsletter.unsubscribe",
			unsubscribe_params = {"name": self.email_group},
			send_priority = 0)

		if not frappe.flags.in_test:
			frappe.db.auto_commit_on_many_writes = False

	def get_recipients(self):
		"""Get recipients from Email Group"""
		return [d.email for d in frappe.db.get_all("Email Group Member", ["email"],
			{"unsubscribed": 0, "email_group": self.email_group})]

	def validate_send(self):
		if self.get("__islocal"):
			throw(_("Please save the Newsletter before sending"))
		check_email_limit(self.recipients)


@frappe.whitelist(allow_guest=True)
def unsubscribe(email, name):
	if not verify_request():
		return

	subs_id = frappe.db.get_value("Email Group Member", {"email": email, "email_group": name})
	if subs_id:
		subscriber = frappe.get_doc("Email Group Member", subs_id)
		subscriber.unsubscribed = 1
		subscriber.save(ignore_permissions=True)

	frappe.db.commit()

	return_unsubscribed_page(email)

def return_unsubscribed_page(email):
	frappe.respond_as_web_page(_("Unsubscribed"), _("{0} has been successfully unsubscribed from this list.").format(email))

def create_lead(email_id):
	"""create a lead if it does not exist"""
	from email.utils import parseaddr
	from frappe.model.naming import get_default_naming_series
	real_name, email_id = parseaddr(email_id)

	if frappe.db.get_value("Lead", {"email_id": email_id}):
		return

	lead = frappe.get_doc({
		"doctype": "Lead",
		"email_id": email_id,
		"lead_name": real_name or email_id,
		"status": "Lead",
		"naming_series": get_default_naming_series("Lead"),
		"company": frappe.db.get_default("Company"),
		"source": "Email"
	})
	lead.insert()


@frappe.whitelist(allow_guest=True)
def subscribe(email):
	url = frappe.utils.get_url("/api/method/frappe.email.doctype.newsletter.newsletter.confirm_subscription") +\
		"?" + get_signed_params({"email": email})

	messages = (
		_("Thank you for your interest in subscribing to our updates"),
		_("Please verify your Email Address"),
		url,
		_("Click here to verify")
	)

	content = """
	<p>{0}. {1}.</p>
	<p><a href="{2}">{3}</a></p>
	"""

	frappe.sendmail(email, subject=_("Confirm Your Email"), content=content.format(*messages))

@frappe.whitelist(allow_guest=True)
def confirm_subscription(email):
	if not verify_request():
		return

	if not frappe.db.exists("Email Group", _("Website")):
		frappe.get_doc({
			"doctype": "Email Group",
			"title": _("Website")
		}).insert(ignore_permissions=True)


	frappe.flags.ignore_permissions = True

	add_subscribers(_("Website"), email)
	frappe.db.commit()

	frappe.respond_as_web_page(_("Confirmed"), _("{0} has been successfully added to our Email Group.").format(email))


def send_newsletter(newsletter):
	try:
		doc = frappe.get_doc("Newsletter", newsletter)
		doc.queue_all()

	except:
		frappe.db.rollback()

		# wasn't able to send emails :(
		doc.db_set("email_sent", 0)
		frappe.db.commit()

		log("send_newsletter")

		raise

	else:
		frappe.db.commit()



