from datetime import datetime
from decimal import Decimal

from django.db.models import Subquery, OuterRef, Sum, F
from django.utils.formats import date_format
from django.utils.translation import pgettext_lazy

from pretix.base.exporter import MultiSheetListExporter
from pretix.base.models import OrderPosition, Invoice, InvoiceLine
from pretix.helpers.iter import chunked_iterable


class BjrExporter(MultiSheetListExporter):
    @property
    def description(self) -> str:
        return super().description

    identifier = "exporterbjr"
    verbose_name = "BJR AEJ/JBM Export"
    category = pgettext_lazy('export_category', 'Order data')
    featured = True

    @property
    def sheets(self):
        return (
            ('aej', 'AEJ'),
            ('jbm', 'JBM'),
            ('team', 'Teamer'),
            ('belege', "Belegliste")
        )

    def iterate_sheet(self, form_data, sheet):
        if sheet == 'aej' or sheet == 'jbm' or sheet == 'team':
            return self.iterate_positions(sheet)
        elif sheet == 'belege':
            return self.iterate_belege()

    # Based on iterate_positions in orderlist.py from pretix
    def iterate_positions(self, sheet: str):
        headers = [
            "Produkt",
            "Nachname",
            "Vorname",
            "w",
            "m",
            "d",
            "PLZ, Ort",
        ]
        if sheet == 'aej':
            headers += [
                "AEJ: 15-<18",
                "AEJ: 18-<27",
                "AEJ: >=27",
            ]
        elif sheet == 'jbm':
            headers += [
                "JBM: <10",
                "JBM: 10-<14",
                "JBM: 14-<18",
                "JBM: 18-<=26",
            ]
        elif sheet == 'team':
            headers += [
                "Alter"
            ]
        yield headers

        base_qs = OrderPosition.all.filter(
            order__event__in=self.events,
        )
        qs = base_qs.select_related(
            'order', 'order__invoice_address', 'order__customer', 'item', 'variation',
            'voucher', 'tax_rule'
        ).prefetch_related(
            'answers', 'answers__question', 'answers__options'
        )

        all_ids = list(base_qs.order_by('order__datetime', 'positionid').values_list('pk', flat=True))
        for ids in chunked_iterable(all_ids, 10000):
            ops = sorted(qs.filter(id__in=ids), key=lambda k: ids.index(k.pk))
            for op in ops:
                row = [
                    str(op.item),
                    op.attendee_name_parts['family_name'],
                    op.attendee_name_parts['given_name']
                ]

                row += self._get_gender_cols(op)
                row += self._get_plzort_cols(op)
                row += self._get_age_cols(op, sheet)
                yield row

    @staticmethod
    def _get_gender_cols(op):
        gender_answer = (a.answer for a in op.answers.all() if a.question.identifier == "Geschlecht")
        gender = next(gender_answer, "")
        return [
            "X" if gender == "w" else "?" if gender == "" else "",
            "X" if gender == "m" else "?" if gender == "" else "",
            "X" if gender == "d" else "?" if gender == "" else "",
        ]

    @staticmethod
    def _get_plzort_cols(op):
        plz_answer = (a.answer for a in op.answers.all() if a.question.identifier == "PLZ")
        plz = next(plz_answer, "?????")
        ort_answer = (a.answer for a in op.answers.all() if a.question.identifier == "Ort")
        ort = next(ort_answer, "?????")
        return [
            plz + " " + ort,
        ]

    @staticmethod
    def _get_age_cols(op, sheet: str):
        order = op.order
        age_answer = (a.answer for a in op.answers.all() if a.question.identifier == "Alter")
        age = int(next(age_answer, "-1"))
        if age < 0:
            geburtsdatum_answer = (a.answer for a in op.answers.all() if a.question.identifier == "Geburtsdatum")
            geburtsdatum = next(geburtsdatum_answer, None)
            if geburtsdatum is not None:
                event_start = order.event.date_from.date()
                geburtsdatum_date = datetime.strptime(geburtsdatum, "%Y-%m-%d").date()
                age = (event_start.year - geburtsdatum_date.year
                       - ((event_start.month, event_start.day)
                          < (geburtsdatum_date.month, geburtsdatum_date.day)))
        if sheet == 'aej':
            return [
                "X" if 15 <= age < 18 else "?" if age < 0 else "",
                "X" if 18 <= age < 27 else "?" if age < 0 else "",
                "X" if 27 <= age else "?" if age < 0 else "",
            ]
        elif sheet == 'jbm':
            return [
                "X" if 0 < age < 10 else "?" if age < 0 else "",
                "X" if 10 <= age < 14 else "?" if age < 0 else "",
                "X" if 14 <= age < 18 else "?" if age < 0 else "",
                "X" if 18 <= age <= 26 else "?" if age < 0 else "",
            ]
        elif sheet == 'team':
            return [
                age if age > 0 else "?"
            ]

    # Based on iterate_sheet in invoices.py from pretix
    def iterate_belege(self):
        headers = [
            "Belegnr.",
            "Belegdatum",
            "Einzahler*in",
            "Verwendungszweck",
            "Betrag",
            "Anmerkung",
            ]
        yield headers

        base_qs = Invoice.objects.filter(event__in=self.events).select_related('order')

        qs = base_qs.select_related(
            'order', 'refers'
        ).prefetch_related('order__payments').annotate(
            total_gross=Subquery(
                InvoiceLine.objects.filter(
                    invoice=OuterRef('pk')
                ).order_by().values('invoice').annotate(
                    s=Sum('gross_value')
                ).values('s')
            ),
            total_net=Subquery(
                InvoiceLine.objects.filter(
                    invoice=OuterRef('pk')
                ).order_by().values('invoice').annotate(
                    s=Sum(F('gross_value') - F('tax_value'))
                ).values('s')
            )
        )

        all_ids = base_qs.order_by('full_invoice_no').values_list('pk', flat=True)
        for ids in chunked_iterable(all_ids, 1000):
            invs = sorted(qs.filter(id__in=ids), key=lambda k: ids.index(k.pk))
            for i in invs:
                yield [
                    i.full_invoice_no,
                    date_format(i.date, "SHORT_DATE_FORMAT"),
                    i.invoice_to_company or i.invoice_to_name,
                    "",
                    i.total_gross if i.total_gross else Decimal('0.00'),
                    "Stornierung von " + i.refers.full_invoice_no if i.is_cancellation and i.refers else "",
                ]
