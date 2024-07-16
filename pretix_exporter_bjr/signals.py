# Register your receivers here
from django.dispatch import receiver

from pretix.base.signals import register_data_exporters


@receiver(register_data_exporters, dispatch_uid="exporter_bjr")
def register_data_exporter(sender, **kwargs):
    from pretix_exporter_bjr.bjr_exporter import BjrExporter

    return BjrExporter
