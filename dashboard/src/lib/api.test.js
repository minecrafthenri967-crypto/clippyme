// submitProcessJob/submitBatchJob: zernio_profile threading from
// optsToPreselections's output into the actual HTTP request body, so a
// campaign chosen in the Create tab reaches job_artifacts.save_job_campaign
// on the backend (see history_service.scan_history's zernioProfile field).
import { test, expect, vi, beforeEach } from 'vitest';

vi.mock('./apiToken', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from './apiToken';
import { submitProcessJob, submitBatchJob } from './api.js';

function okJson(body) {
  return { ok: true, json: async () => body };
}

beforeEach(() => {
  vi.clearAllMocks();
  apiFetch.mockResolvedValue(okJson({ job_id: 'x' }));
});

test('submitProcessJob (url): zernio_profile rides the JSON body', async () => {
  await submitProcessJob(
    { type: 'url', payload: 'https://youtu.be/x', preselections: { zernio_profile: 'dja' } },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.zernio_profile).toBe('dja');
});

test('submitProcessJob (url): "default" is still sent explicitly', async () => {
  await submitProcessJob(
    { type: 'url', payload: 'https://youtu.be/x', preselections: { zernio_profile: 'default' } },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.zernio_profile).toBe('default');
});

test('submitProcessJob (url): blank/missing zernio_profile omits the field', async () => {
  await submitProcessJob({ type: 'url', payload: 'https://youtu.be/x' }, 'key');
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect('zernio_profile' in body).toBe(false);
});

test('submitProcessJob (file): zernio_profile rides the FormData body', async () => {
  await submitProcessJob(
    {
      type: 'file',
      payload: new File(['x'], 'v.mp4'),
      preselections: { zernio_profile: 'ebay_live' },
    },
    'key',
  );
  const formData = apiFetch.mock.calls[0][1].body;
  expect(formData.get('zernio_profile')).toBe('ebay_live');
});

test('submitBatchJob: zernio_profile rides the JSON body', async () => {
  apiFetch.mockResolvedValue(okJson({ jobs: [], total: 0 }));
  await submitBatchJob(
    { urls: ['https://youtu.be/x'], preselections: { zernio_profile: 'dja' } },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.zernio_profile).toBe('dja');
});

const FACECAM_BOX = { x: 0.6, y: 0.05, w: 0.35, h: 0.3 };

test('submitProcessJob (url): manual gaming facecam box rides the JSON body', async () => {
  await submitProcessJob(
    {
      type: 'url', payload: 'https://youtu.be/x',
      preselections: { reframe_mode: 'gaming', gaming_facecam_box: FACECAM_BOX },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.gaming_facecam_box).toEqual(FACECAM_BOX);
});

test('submitProcessJob (url): no facecam box omits the field', async () => {
  await submitProcessJob(
    { type: 'url', payload: 'https://youtu.be/x', preselections: { reframe_mode: 'gaming' } },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect('gaming_facecam_box' in body).toBe(false);
});

test('submitProcessJob (url): manual facecam box outside gaming mode is not sent', async () => {
  await submitProcessJob(
    {
      type: 'url', payload: 'https://youtu.be/x',
      preselections: { reframe_mode: 'auto', gaming_facecam_box: FACECAM_BOX },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect('gaming_facecam_box' in body).toBe(false);
});

test('submitProcessJob (file): manual gaming facecam box rides the FormData body as a JSON string', async () => {
  await submitProcessJob(
    {
      type: 'file', payload: new File(['x'], 'v.mp4'),
      preselections: { reframe_mode: 'gaming', gaming_facecam_box: FACECAM_BOX },
    },
    'key',
  );
  const formData = apiFetch.mock.calls[0][1].body;
  expect(JSON.parse(formData.get('gaming_facecam_box'))).toEqual(FACECAM_BOX);
});

test('submitBatchJob: manual gaming facecam box rides the JSON body', async () => {
  apiFetch.mockResolvedValue(okJson({ jobs: [], total: 0 }));
  await submitBatchJob(
    {
      urls: ['https://youtu.be/x'],
      preselections: { reframe_mode: 'gaming', gaming_facecam_box: FACECAM_BOX },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.gaming_facecam_box).toEqual(FACECAM_BOX);
});
