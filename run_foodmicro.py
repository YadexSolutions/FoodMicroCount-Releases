"""Entry point for the frozen desktop application."""
import contextlib
import os
import sys
import traceback


def launch():
    from foodmicro.app import main
    main()


if __name__ == '__main__':
    smoke_log = os.environ.get('FOODMICRO_SMOKE_LOG') if '--smoke-test' in sys.argv else None
    if smoke_log:
        with open(smoke_log, 'w', encoding='utf-8') as stream:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                try:
                    launch()
                except Exception:
                    traceback.print_exc(file=stream)
                    sys.exit(1)
    else:
        launch()
