"""Prefect release flow: durable preparation, fresh file/gate checks, idempotent commit."""
import argparse
import os
from pathlib import Path


def execute(root, report_name='report-B.json', manifest_name='manifest-B.json',
            fail_after_prepare=False, fail_after_pointer=False):
    root = Path(root).resolve()
    os.environ.setdefault('PREFECT_HOME', str(root / '.prefect'))
    os.environ.setdefault('PREFECT_SERVER_ANALYTICS_ENABLED', 'false')
    os.environ.setdefault('PREFECT_LOCAL_STORAGE_PATH', str(root / '.prefect-results'))
    from prefect import flow, task
    from prefect.cache_policies import NO_CACHE
    from lab import prepare
    from publication import attempt, publish_once, read
    from release import cache_key, validate_release, verify_artifact

    def preparation_key(context, parameters):
        workspace = Path(parameters['workspace'])
        return cache_key(workspace / 'input.txt', read(workspace / 'config.json'))

    @task(name='validate-input', cache_key_fn=preparation_key, persist_result=True,
          result_serializer='json', result_storage=root / '.prefect-results')
    def preparation(workspace):
        return prepare(Path(workspace))

    @task(name='read-existing-training-artifact', cache_policy=NO_CACHE, retries=0)
    def artifact(workspace, manifest_file):
        m = read(Path(workspace) / manifest_file)
        verify_artifact(m, workspace)
        # No training here: this is an immutable artifact descriptor.
        return m

    @task(name='full-evaluation-gate', cache_policy=NO_CACHE, retries=0)
    def gate(workspace, m, report_file):
        report = read(Path(workspace) / report_file)
        if m['runtime']['base_revision'] == 'fixture-only':
            if report.get('synthetic') is not True:
                raise ValueError('Fixture evaluation must be marked synthetic')
        else:
            from gate_bridge import verify_release_report
            verify_release_report(m, report, Path(workspace))
        validate_release(m, report)
        return report

    @task(name='publish-local-pointer', cache_policy=NO_CACHE, retries=0)
    def publication(workspace, m, r, prepared):
        return publish_once(Path(workspace), m, r, prepared, fail_after_pointer)

    @flow(name='hw8910-release', retries=0, log_prints=True)
    def release_flow():
        from prefect.runtime import flow_run
        try:
            state = preparation(str(root), return_state=True)
            prepared = state.result()
            print('PREPARATION_STATE', state.name, prepared['input_sha256'])
            if fail_after_prepare:
                raise RuntimeError('Injected failure after preparation')
            m = artifact(str(root), manifest_name)
            report = gate(str(root), m, report_name)
            event = publication(str(root), m, report, prepared)
            attempt(root, 'published', flow_run_id=str(flow_run.id), release_id=event['release_id'])
            return event
        except Exception as exc:
            attempt(root, 'failed', flow_run_id=str(flow_run.id), error=f'{type(exc).__name__}: {exc}')
            raise
    return release_flow()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--workspace', type=Path, default=Path('.lab'))
    p.add_argument('--report', default='report-B.json')
    p.add_argument('--manifest', default='manifest-B.json')
    p.add_argument('--fail-after-prepare', action='store_true')
    p.add_argument('--fail-after-pointer', action='store_true')
    args = p.parse_args()
    execute(args.workspace, args.report, args.manifest, args.fail_after_prepare, args.fail_after_pointer)


if __name__ == '__main__':
    main()
