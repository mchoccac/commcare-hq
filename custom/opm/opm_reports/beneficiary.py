"""
Fluff calculators that pertain to specific cases/beneficiaries (mothers)
These are used in the Beneficiary Payment Report and Conditions Met Report
"""
import re
import datetime
from decimal import Decimal
from django.core.urlresolvers import reverse
from dimagi.utils.dates import months_between, first_of_next_month

from dimagi.utils.dates import add_months
from dimagi.utils.decorators.memoized import memoized
from django.utils.translation import ugettext as _

from corehq.util.translation import localize

from .constants import *


EMPTY_FIELD = "---"
M_ATTENDANCE_Y = 'attendance_vhnd_y.png'
M_ATTENDANCE_N = 'attendance_vhnd_n.png'
C_ATTENDANCE_Y = 'child_attendance_vhnd_y.png'
C_ATTENDANCE_N = 'child_attendance_vhnd_n.png'
M_WEIGHT_Y = 'woman_checking_weight_yes.png'
M_WEIGHT_N = 'woman_checking_weight_no.png'
C_WEIGHT_Y = 'child_weight_y.png'
C_WEIGHT_N = 'child_weight_n.png'
MEASLEVACC_Y = 'child_child_measlesvacc_y.png'
MEASLEVACC_N = 'child_child_measlesvacc_n.png'
C_REGISTER_Y = 'child_child_register_y.png'
C_REGISTER_N = 'child_child_register_n.png'
CHILD_WEIGHT_Y = 'child_weight_2_y.png'
CHILD_WEIGHT_N = 'child_weight_2_n.png'
IFA_Y = 'ifa_receive_y.png'
IFA_N = 'ifa_receive_n.png'
EXCBREASTFED_Y = 'child_child_excbreastfed_y.png'
EXCBREASTFED_N = 'child_child_excbreastfed_n.png'
ORSZNTREAT_Y = 'child_orszntreat_y.png'
ORSZNTREAT_N = 'child_orszntreat_n.png'
GRADE_NORMAL_Y = 'grade_normal_y.png'
GRADE_NORMAL_N = 'grade_normal_n.png'
SPACING_PROMPT_Y = 'birth_spacing_prompt_y.png'
SPACING_PROMPT_N = 'birth_spacing_prompt_n.png'
VHND_NO = 'VHND_no.png'


# This is just a string processing function, not moving to class
indexed_child = lambda prop, num: prop.replace("child1", "child" + str(num))


class OPMCaseRow(object):

    def __init__(self, case, report, child_index=1):
        self.child_index = child_index
        self.case = case
        self.report = report
        self.data_provider = report.data_provider
        self.block = report.block.lower()
        self.datespan = self.report.datespan

        if report.snapshot is not None:
            report.filter(
                lambda key: self.case[key],
                # case.awc_name, case.block_name
                [('awc_name', 'awcs'), ('block_name', 'block'), ('owner_id', 'gp'), ('closed', 'is_open')],
            )
        if not report.is_rendered_as_email:
            self.img_elem = '<div style="width:100px !important;"><img src="/static/opm/img/%s"></div>'
        else:
            self.img_elem = '<div><img src="/static/opm/img/%s"></div>'

        self.set_case_properties()
        self.add_extra_children()

        if report.is_rendered_as_email:
            with localize('hin'):
                self.status = _(self.status)

    def condition_image(self, image_y, image_n, condition):
        if condition is None:
            return ''
        elif condition is True:
            return self.img_elem % image_y
        elif condition is False:
            return self.img_elem % image_n

    @property
    def preg_attended_vhnd(self):
        # in month 9 they always meet this condition
        if self.preg_month == 9:
            return True
        elif not self.vhnd_available:
            return True
        elif 9 > self.preg_month > 3:
            vhnd_attendance = {
                4: self.case_property('attendance_vhnd_1', 0),
                5: self.case_property('attendance_vhnd_2', 0),
                6: self.case_property('attendance_vhnd_3', 0),
                7: self.case_property('month_7_attended', 0),
                8: self.case_property('month_8_attended', 0)
            }
            return vhnd_attendance[self.preg_month] == '1'
        else:
            return False

    @property
    def child_attended_vhnd(self):
        if self.child_age == 1:
            return True
        elif not self.vhnd_available:
            return True
        else:
            return any(
                form.form.get(indexed_child('child1_vhndattend_calc', self.child_index)) == 'received'
                for form in self.forms
                if form.xmlns in CHILDREN_FORMS and self.form_in_range(form)
            )

    @property
    def preg_weighed(self):
        if self.preg_month == 6:
            return self.case_property('weight_tri_1') == 'received'
        elif self.preg_month == 9:
            return self.case_property('weight_tri_2') == 'received'

    def filtered_forms(self, xmlns_or_list=None, months_before=None, months_after=None):
        """
        Returns a list of forms filtered by xmlns if specified
        and from the previous number of calendar months if specified
        """
        if isinstance(xmlns_or_list, basestring):
            xmlns_list = [xmlns_or_list]
        else:
            xmlns_list = xmlns_or_list or []

        reference_date = self.datespan.enddate
        if months_before is not None:
            new_year, new_month = add_months(reference_date.year, reference_date.month, -months_before)
            start = first_of_next_month(datetime.datetime(new_year, new_month, 1))
        else:
            start = None

        if months_after is not None:
            new_year, new_month = add_months(reference_date.year, reference_date.month, months_after)
            end = first_of_next_month(datetime.datetime(new_year, new_month, 1))
        else:
            end = first_of_next_month(reference_date)

        def check_form(form):
            if xmlns_list and form.xmlns not in xmlns_list:
                return False
            if start and form.received_on < start:
                return False
            if end and form.received_on >= end:
                return False
            return True
        return filter(check_form, self.forms)

    @property
    def child_growth_calculated(self):
        if self.child_age % 3 == 0:
            for form in self.filtered_forms(CHILDREN_FORMS, 3):
                prop = indexed_child('child1_growthmon_calc', self.child_index)
                if form.form.get(prop) == 'received':
                    return True
            return False

    @property
    def preg_received_ifa(self):
        if self.preg_month == 6:
            if self.block == "atri":
                return self.case_property('ifa_tri_1', 0) == 'received'

    @property
    def child_received_ors(self):
        if self.child_age % 3 == 0:
            for form in self.filtered_forms(CHILDREN_FORMS, 3):
                prop = indexed_child('child1_child_orszntreat', self.child_index)
                if form.form.get(prop) == '0':
                    return False
            return True

    @property
    def child_weighed_once(self):
        if self.child_age == 3:
            def _test(form):
                return form.xpath(indexed_child('form/child1/child1_child_weight', self.child_index)) == '1'

            return any(
                _test(form)
                for form in self.filtered_forms(CFU1_XMLNS, 2, 1)
            )

    @property
    def child_birth_registered(self):
        if self.child_age == 6:
            def _test(form):
                return form.xpath(indexed_child('form/child1/child1_child_register', self.child_index)) == '1'

            return any(
                _test(form)
                for form in self.filtered_forms(CFU1_XMLNS, 2, 1)
            )

    @property
    def child_received_measles_vaccine(self):
        if self.child_age == 12:
            def _test(form):
                return form.xpath(indexed_child('form/child1/child1_child_measlesvacc', self.child_index)) == '1'

            return any(
                _test(form)
                for form in self.filtered_forms([CFU1_XMLNS, CFU2_XMLNS], 2, 1)
            )

    @property
    def child_condition_four(self):
        if self.block == 'atri':
            if self.child_age == 3:
                return self.child_weighed_once
            elif self.child_age == 6:
                return self.child_birth_registered
            elif self.child_age == 12:
                return self.child_received_measles_vaccine

    @property
    def child_breastfed(self):
        if self.child_age == 6 and self.block == 'atri':
            excl_key = indexed_child("child1_child_excbreastfed", self.child_index)
            for form in self.filtered_forms(CHILDREN_FORMS):
                if form.form.get(excl_key) == '0':
                    return False
            return True

    @property
    def live_delivery(self):
        # TODO czue, please verify the dates here, it should only be looking at
        # delivery forms submitted in the last month, but I'm seeing some cases
        # with child_age as 2 or 3, which is inconsistent.  Uncomment the print
        # lines below and run the report to replicate. (block: Atri, gp: Sahora
        # are the filters I'm using)
        for form in self.filtered_forms(DELIVERY_XMLNS, months_before=1):
            outcome = form.form.get('mother_preg_outcome')
            if outcome == '1':
                # print "*"*40, 'live_delivery', "*"*40
                # print self.child_age
                # print form.received_on
                return True
            elif outcome in ['2', '3']:
                return False

    @property
    def birth_spacing_years(self):
        """
        returns None if inapplicable, False if not met, or
        2 for 2 years, or 3 for 3 years.
        """
        # TODO should this actually be [25, 37] ?
        if self.child_age in [24, 36]:
            for form in self.filtered_forms(CHILDREN_FORMS):
                if form.form.get('birth_spacing_prompt') == '1':
                    return False
            return self.child_age/12

    def case_property(self, name, default=None):
        prop = getattr(self.case, name, default)
        if isinstance(prop, basestring) and prop.strip() == "":
            return default
        return prop

    def form_in_range(self, form, adjust_lower=0):
        lower = self.datespan.startdate + datetime.timedelta(days=adjust_lower)
        upper = self.datespan.enddate
        return lower <= form.received_on <= upper

    def set_case_properties(self):
        reporting_date = self.datespan.enddate.date()  # last day of the month
        status = "unknown"
        self.preg_month = None
        self.child_age = None
        window = None
        dod_date = self.case_property('dod')
        edd_date = self.case_property('edd')
        if dod_date is not None:
            if dod_date > reporting_date:
                status = 'pregnant'
                self.preg_month = 9 - (dod_date - reporting_date).days / 30  # edge case
            elif dod_date < reporting_date:
                status = 'mother'
                self.child_age = len(months_between(dod_date, reporting_date))
        elif edd_date is not None:
            if edd_date >= reporting_date:
                status = 'pregnant'
                self.preg_month = 9 - (edd_date - reporting_date).days / 30
            elif edd_date < reporting_date:  # edge case
                raise InvalidRow
        else: #if dod_date is None and edd_date is None:
            raise InvalidRow
        if status == 'pregnant' and (self.preg_month > 3 and self.preg_month < 10):
            self.window = (self.preg_month - 1) / 3
        elif status == 'mother' and (self.child_age > 0 and self.child_age < 37):
            self.window = (self.child_age - 1) / 3 + 1
        else:
            raise InvalidRow
        if (self.child_age is None and self.preg_month is None):
            raise InvalidRow

        self.status = status

        url = reverse("case_details", args=[DOMAIN, self.case_property('_id', '')])
        self.name = "<a href='%s'>%s</a>" % (url, self.case_property('name', EMPTY_FIELD))
        self.awc_name = self.case_property('awc_name', EMPTY_FIELD)
        self.block_name = self.case_property('block_name', EMPTY_FIELD)
        self.husband_name = self.case_property('husband_name', EMPTY_FIELD)
        self.bank_name = self.case_property('bank_name', EMPTY_FIELD)
        self.ifs_code = self.case_property('ifsc', EMPTY_FIELD)
        self.village = self.case_property('village_name', EMPTY_FIELD)
        self.case_id = self.case_property('_id', EMPTY_FIELD)
        self.owner_id = self.case_property('owner_id', '')
        self.closed = self.case_property('closed', False)

        account = self.case_property('bank_account_number', None)
        if isinstance(account, Decimal):
            account = int(account)
        self.account_number = unicode(account) if account else ''
        # fake cases will have accounts beginning with 111
        if re.match(r'^111', self.account_number):
            raise InvalidRow

    @property
    @memoized
    def vhnd_available(self):
        if self.owner_id not in self.data_provider.vhnd_availability:
            raise InvalidRow
        return self.data_provider.vhnd_availability[self.owner_id]

    def add_extra_children(self):
        if self.child_index == 1:
            # app supports up to three children only
            num_children = min(int(self.case_property("live_birth_amount", 1)), 3)
            if num_children > 1:
                extra_child_objects = [(ConditionsMet(self.case, self.report, child_index=num + 2)) for num in range(num_children - 1)]
                self.report.set_extra_row_objects(extra_child_objects)

    @property
    @memoized
    def forms(self):
        return self.case.get_forms()

    @property
    def all_conditions_met(self):
        if not self.vhnd_available:
            return True
        if self.status == 'mother':
            relevant_conditions = [
                self.child_attended_vhnd,
                self.child_growth_calculated,
                self.child_received_ors,
                self.child_condition_four,
                self.child_breastfed,
            ]
        else:
            relevant_conditions = [
                self.preg_attended_vhnd,
                self.preg_weighed,
                self.preg_received_ifa,
            ]
        return False not in relevant_conditions

    @property
    def month_amt(self):
        return MONTH_AMT if self.all_conditions_met else 0

    @property
    def spacing_cash(self):
        return {
            2: TWO_YEAR_AMT,
            3: THREE_YEAR_AMT,
        }.get(self.birth_spacing_years, 0)

    @property
    def cash_amt(self):
        return self.month_amt + self.spacing_cash

    @property
    def cash(self):
        cash_html = '<span style="color: {color};">Rs. {amt}</span>'
        return cash_html.format(
            color="red" if self.cash_amt == 0 else "green",
            amt=self.cash_amt,
        )


class ConditionsMet(OPMCaseRow):
    method_map = {
        "atri": [
            ('name', _("List of Beneficiary"), True),
            ('awc_name', _("AWC Name"), True),
            ('block_name', _("Block Name"), True),
            ('husband_name', _("Husband Name"), True),
            ('status', _("Current status"), True),
            ('preg_month', _('Pregnancy Month'), True),
            ('child_name', _("Child Name"), True),
            ('child_age', _("Child Age"), True),
            ('window', _("Window"), True),
            ('one', _("1"), True),
            ('two', _("2"), True),
            ('three', _("3"), True),
            ('four', _("4"), True),
            ('five', _("5"), True),
            ('cash', _("Payment Amount"), True),
            ('case_id', _('Case ID'), True),
            ('owner_id', _("Owner Id"), False),
            ('closed', _('Closed'), False)
        ],
        'wazirganj': [
            ('name', _("List of Beneficiary"), True),
            ('awc_name', _("AWC Name"), True),
            ('block_name', _("Block Name"), True),
            ('husband_name', _("Husband Name"), True),
            ('status', _("Current status"), True),
            ('preg_month', _('Pregnancy Month'), True),
            ('child_name', _("Child Name"), True),
            ('child_age', _("Child Age"), True),
            ('window', _("Window"), True),
            ('one', _("1"), True),
            ('two', _("2"), True),
            ('three', _("3"), True),
            ('four', _("4"), True),
            ('cash', _("Payment Amount"), True),
            ('case_id', _('Case ID'), True),
            ('owner_id', _("Owner Id"), False),
            ('closed', _('Closed'), False)
        ]
    }

    def __init__(self, case, report, child_index=1):
        super(ConditionsMet, self).__init__(case, report, child_index=child_index)
        if self.status == 'mother':
            self.child_name = self.case_property(indexed_child("child1_name", child_index), EMPTY_FIELD)
            self.preg_month = EMPTY_FIELD
            self.one = self.condition_image(C_ATTENDANCE_Y, C_ATTENDANCE_N, self.child_attended_vhnd)
            self.two = self.condition_image(C_WEIGHT_Y, C_WEIGHT_N, self.child_growth_calculated)
            self.three = self.condition_image(ORSZNTREAT_Y, ORSZNTREAT_N, self.child_received_ors)
            self.four = self.condition_image(MEASLEVACC_Y, MEASLEVACC_N, self.child_condition_four)
            self.five = self.condition_image(EXCBREASTFED_Y, EXCBREASTFED_N, self.child_breastfed)
        elif self.status == 'pregnant':
            self.child_name = EMPTY_FIELD
            self.one = self.condition_image(M_ATTENDANCE_Y, M_ATTENDANCE_N, self.preg_attended_vhnd)
            self.two = self.condition_image(M_WEIGHT_Y, M_WEIGHT_N, self.preg_weighed)
            self.three = self.condition_image(IFA_Y, IFA_N, self.preg_received_ifa)
            self.four = ''
            self.five = ''

        if self.child_age in (24, 36):
            if self.birth_spacing_years:
                self.five = self.img_elem % SPACING_PROMPT_Y
            elif self.birth_spacing_years is False:
                self.five = self.img_elem % SPACING_PROMPT_N
            else:
                self.five = ''

        if not self.vhnd_available:
            # TODO what if they don't meet the other conditions?
            met_or_not = True
            self.one = self.img_elem % VHND_NO
            self.two, self.three, self.four, self.five = '','','',''


class Beneficiary(OPMCaseRow):
    """
    Constructor object for each row in the Beneficiary Payment Report
    """
    method_map = [
        # If you need to change any of these names, keep the key intact
        ('name', _("List of Beneficiaries"), True),
        ('husband_name', _("Husband Name"), True),
        ('awc_name', _("AWC Name"), True),
        ('bank_name', _("Bank Name"), True),
        ('ifs_code', _("IFS Code"), True),
        ('account_number', _("Bank Account Number"), True),
        ('block_name', _("Block Name"), True),
        ('village', _("Village Name"), True),
        ('bp1_cash', _("Birth Preparedness Form 1"), True),
        ('bp2_cash', _("Birth Preparedness Form 2"), True),
        ('delivery_cash', _("Delivery Form"), True),
        ('child_cash', _("Child Followup Form"), True),
        ('spacing_cash', _("Birth Spacing Bonus"), True),
        ('total', _("Amount to be paid to beneficiary"), True),
        ('owner_id', _("Owner ID"), False)
    ]

    def __init__(self, case, report):
        super(Beneficiary, self).__init__(case, report)
        self.bp1_cash = MONTH_AMT if self.bp1 else 0
        self.bp2_cash = MONTH_AMT if self.bp2 else 0
        self.delivery_cash = MONTH_AMT if self.live_delivery else 0
        self.child_cash = MONTH_AMT if self.child_followup else 0
        self.total = min(
            MONTH_AMT,
            self.bp1_cash + self.bp2_cash + self.delivery_cash + self.child_cash
        )
        # Show only cases that require payment
        if self.total == 0:
            raise InvalidRow

    @property
    def bp1(self):
        if self.window == 1:
            return self.bp_conditions

    @property
    def bp2(self):
        if self.window == 2:
            return self.bp_conditions

    @property
    def bp_conditions(self):
        if self.status == "pregnant":
            return False not in [
                self.preg_attended_vhnd,
                self.preg_weighed,
                self.preg_received_ifa,
            ]

    @property
    def child_followup(self):
        """
        wazirganj - total_soft_conditions = 1
        """
        if self.status == 'mother':
            return False not in [
                self.child_attended_vhnd,
                self.child_received_ors,
                self.child_growth_calculated,
                self.child_weighed_once,
                self.child_birth_registered,
                self.child_received_measles_vaccine,
                self.child_breastfed,
            ]
