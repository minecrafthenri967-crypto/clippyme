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

test('submitProcessJob (url): manual gaming facecam position/size ride the JSON body', async () => {
  await submitProcessJob(
    {
      type: 'url', payload: 'https://youtu.be/x',
      preselections: { reframe_mode: 'gaming', gaming_facecam_position: 'top-left', gaming_facecam_size: 'L' },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.gaming_facecam_position).toBe('top-left');
  expect(body.gaming_facecam_size).toBe('L');
});

test('submitProcessJob (url): auto facecam position omits both fields', async () => {
  await submitProcessJob(
    {
      type: 'url', payload: 'https://youtu.be/x',
      preselections: { reframe_mode: 'gaming', gaming_facecam_position: 'auto' },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect('gaming_facecam_position' in body).toBe(false);
  expect('gaming_facecam_size' in body).toBe(false);
});

test('submitProcessJob (url): manual facecam position outside gaming mode is not sent', async () => {
  await submitProcessJob(
    {
      type: 'url', payload: 'https://youtu.be/x',
      preselections: { reframe_mode: 'auto', gaming_facecam_position: 'top-left' },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect('gaming_facecam_position' in body).toBe(false);
});

test('submitProcessJob (file): manual gaming facecam position/size ride the FormData body', async () => {
  await submitProcessJob(
    {
      type: 'file', payload: new File(['x'], 'v.mp4'),
      preselections: { reframe_mode: 'gaming', gaming_facecam_position: 'bottom-right', gaming_facecam_size: 'S' },
    },
    'key',
  );
  const formData = apiFetch.mock.calls[0][1].body;
  expect(formData.get('gaming_facecam_position')).toBe('bottom-right');
  expect(formData.get('gaming_facecam_size')).toBe('S');
});

test('submitBatchJob: manual gaming facecam position/size ride the JSON body', async () => {
  apiFetch.mockResolvedValue(okJson({ jobs: [], total: 0 }));
  await submitBatchJob(
    {
      urls: ['https://youtu.be/x'],
      preselections: { reframe_mode: 'gaming', gaming_facecam_position: 'top-right', gaming_facecam_size: 'M' },
    },
    'key',
  );
  const body = JSON.parse(apiFetch.mock.calls[0][1].body);
  expect(body.gaming_facecam_position).toBe('top-right');
  expect(body.gaming_facecam_size).toBe('M');
});
