from baserow.config.celery import app


@app.task(bind=True, queue="export")
def license_check(self):
    """
    Periodic license check task - disabled.
    """

    pass


# noinspection PyUnusedLocal
@app.on_after_finalize.connect
def setup_periodic_tasks(sender, **kwargs):
    # Periodic license check disabled.
    pass
