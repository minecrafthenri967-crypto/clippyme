// PublishModal — caption presets: a dropdown to reuse a saved caption
// template (e.g. one per seller in a multi-account clipping campaign) and a
// "Save as preset" control that persists the current caption text.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PublishModal } from './publish.jsx';

vi.mock('./realApi', () => ({
  clipVideoSrc: () => '/videos/j/clip_1.mp4',
  publishClip: vi.fn(async () => ({})),
  getZernio: vi.fn(async () => ({
    configured: true,
    accounts: { tiktok: 'tt-1', instagram: '', youtube: '' },
  })),
  getZernioProfiles: vi.fn(async () => ({
    profiles: [{ id: 'default', label: 'Default', configured: true }],
  })),
  getCaptionPresets: vi.fn(async () => ({ presets: [] })),
  saveCaptionPreset: vi.fn(async () => ({
    presets: [{ id: 'johns_breaks', label: "John's Breaks", text: '#eBayLive #JohnsBreaks' }],
  })),
}));

const CLIPS = [{ _idx: 0, video_title_for_youtube_short: 'Big Pull' }];

beforeEach(() => {
  vi.clearAllMocks();
});

test('no presets: dropdown is hidden, save-as-preset control is available', async () => {
  render(<PublishModal clips={CLIPS} jobId="job1" onClose={() => {}} pushToast={() => {}} />);
  await screen.findByRole('button', { name: /Save as preset/ });
  expect(screen.queryByLabelText('Caption preset')).not.toBeInTheDocument();
});

test('existing presets: dropdown appears and applying one fills the caption field', async () => {
  const { getCaptionPresets } = await import('./realApi');
  getCaptionPresets.mockResolvedValueOnce({
    presets: [{ id: 'johns_breaks', label: "John's Breaks", text: '#eBayLive #JohnsBreaks @johns #tradingcards' }],
  });
  render(<PublishModal clips={CLIPS} jobId="job1" onClose={() => {}} pushToast={() => {}} />);
  await screen.findByLabelText('Caption preset');
  fireEvent.change(screen.getByLabelText('Caption preset'), { target: { value: 'johns_breaks' } });
  const textarea = document.querySelector('textarea.ta');
  await waitFor(() => expect(textarea.value).toBe('#eBayLive #JohnsBreaks @johns #tradingcards'));
});

test('save as preset: types a name, saves, and the new preset is persisted', async () => {
  const { saveCaptionPreset } = await import('./realApi');
  render(<PublishModal clips={CLIPS} jobId="job1" onClose={() => {}} pushToast={() => {}} />);
  await screen.findByRole('button', { name: /Save as preset/ });

  const textarea = document.querySelector('textarea.ta');
  fireEvent.change(textarea, { target: { value: '#eBayLive #JohnsBreaks' } });
  fireEvent.click(screen.getByRole('button', { name: /Save as preset/ }));
  fireEvent.change(screen.getByLabelText('Preset name'), { target: { value: "John's Breaks" } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));

  await waitFor(() => expect(saveCaptionPreset).toHaveBeenCalledWith(
    'john_s_breaks', "John's Breaks", '#eBayLive #JohnsBreaks',
  ));
});
