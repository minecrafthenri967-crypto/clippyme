// LiveMonitorView — pins: per-platform channel validation gates Start, the
// monitor list renders multiple concurrent monitors, and youtube forces VOD.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { LiveMonitorView } from './live.jsx';

const mockStatus = vi.fn();

vi.mock('./realApi', () => ({
  getZernio: vi.fn(async () => ({
    configured: true,
    accounts: { tiktok: 'tt-1', instagram: '', youtube: '' },
  })),
  getZernioProfiles: vi.fn(async () => ({
    profiles: [{ id: 'default', label: 'Default', configured: true }],
  })),
  startLiveMonitor: vi.fn(async () => ({ id: 'kick:xqc', running: true, state: 'waiting_live' })),
  stopLiveMonitor: vi.fn(async () => ({ running: false, state: 'idle' })),
  getLiveMonitorStatus: (...args) => mockStatus(...args),
  updateMonitorConfig: vi.fn(async () => ({ monitor: {} })),
  setMonitorPublishing: vi.fn(async () => ({ publishing_enabled: true })),
  listFonts: vi.fn(async () => ({ fonts: [] })),
  probeLiveMonitor: vi.fn(async () => ({ ok: true, mode: 'live', live: true })),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockStatus.mockResolvedValue({ monitors: [] });
});

test('start is disabled until a valid channel is entered', async () => {
  render(<LiveMonitorView />);
  await screen.findByLabelText('Channel');
  const startBtn = screen.getByRole('button', { name: /Start monitor/ });
  expect(startBtn).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(startBtn).not.toBeDisabled());
});

test('invalid channel shows an inline error after blur', () => {
  render(<LiveMonitorView />);
  const input = screen.getByLabelText('Channel');
  fireEvent.change(input, { target: { value: 'has space' } });
  fireEvent.blur(input);
  expect(screen.getByText(/lowercase letters, numbers/)).toBeInTheDocument();
});

test('empty channel still blocks start after touching the field', () => {
  render(<LiveMonitorView />);
  const input = screen.getByLabelText('Channel');
  fireEvent.blur(input);
  expect(screen.getByText('Channel is required')).toBeInTheDocument();
});

test('start form is always visible alongside a running monitor', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', segments_captured: 2, clips_published: 5 }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('Capturing segment')).toBeInTheDocument();
  expect(screen.getByText('xqc')).toBeInTheDocument();
  expect(screen.getByText(/2 segment\(s\) captured/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Start monitor/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Stop/ })).toBeInTheDocument();
});

test('monitor list renders multiple concurrent monitors', async () => {
  mockStatus.mockResolvedValue({
    monitors: [
      { id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' },
      { id: 'youtube:@MrBeast', platform: 'youtube', mode: 'vod', running: true, state: 'watching', channel: '@MrBeast' },
    ],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('xqc')).toBeInTheDocument();
  expect(screen.getByText('@MrBeast')).toBeInTheDocument();
  expect(screen.getByText('Watching for new uploads')).toBeInTheDocument();
  expect(screen.getAllByRole('button', { name: /Stop/ })).toHaveLength(2);
});

test('idle empty list shows "No monitors running."', async () => {
  render(<LiveMonitorView />);
  expect(await screen.findByText('No monitors running.')).toBeInTheDocument();
});

test('selecting YouTube forces VOD mode and hides live-only fields', async () => {
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'YouTube' }));
  await waitFor(() => expect(screen.queryByLabelText('Segment minutes')).toBeNull());
  expect(screen.queryByLabelText('Prelive skip minutes')).toBeNull();
  expect(screen.getByText(/YouTube: clips every new long-form upload/)).toBeInTheDocument();
});

test('test connection is disabled until a channel is entered', async () => {
  render(<LiveMonitorView />);
  await screen.findByLabelText('Channel');
  expect(screen.getByRole('button', { name: /Test connection/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Test connection/ })).not.toBeDisabled());
});

test('test connection reports live state without starting a monitor', async () => {
  const { probeLiveMonitor, startLiveMonitor } = await import('./realApi');
  probeLiveMonitor.mockResolvedValueOnce({ ok: true, mode: 'live', live: true });
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));
  expect(await screen.findByText(/Live right now/)).toBeInTheDocument();
  expect(probeLiveMonitor).toHaveBeenCalledWith({ platform: 'kick', channel: 'xqc', mode: 'live' });
  expect(startLiveMonitor).not.toHaveBeenCalled();
});

test('test connection reports detected VOD items', async () => {
  const { probeLiveMonitor } = await import('./realApi');
  probeLiveMonitor.mockResolvedValueOnce({
    ok: true, mode: 'vod', count: 3,
    sample: [{ id: 'v1', url: 'https://kick.com/video/v1', created_at: '' }],
  });
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'YouTube' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: '@someone' } });
  fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));
  expect(await screen.findByText(/Detected 3 item\(s\)/)).toBeInTheDocument();
});

test('test connection surfaces a failure without crashing the form', async () => {
  const { probeLiveMonitor } = await import('./realApi');
  probeLiveMonitor.mockRejectedValueOnce(new Error('Twitch not configured'));
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'Twitch' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));
  expect(await screen.findByText(/Could not detect: Twitch not configured/)).toBeInTheDocument();
});

test('changing the channel clears a stale probe result', async () => {
  const { probeLiveMonitor } = await import('./realApi');
  probeLiveMonitor.mockResolvedValueOnce({ ok: true, mode: 'live', live: true });
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  fireEvent.click(screen.getByRole('button', { name: /Test connection/ }));
  expect(await screen.findByText(/Live right now/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'someoneelse' } });
  expect(screen.queryByText(/Live right now/)).toBeNull();
});

test('duplicate monitor (409) shows a warning toast', async () => {
  const { startLiveMonitor } = await import('./realApi');
  startLiveMonitor.mockRejectedValueOnce(new Error('monitor already running: kick:xqc'));
  const pushToast = vi.fn();
  render(<LiveMonitorView pushToast={pushToast} />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('warn', expect.stringMatching(/already monitoring/i)));
});

test('banner defaults to Auto and sends null', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toBeNull();
});

test('AI instructions field is included in the start payload', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('AI instructions'), { target: { value: 'find the funniest bits' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].instructions).toBe('find the funniest bits');
});

test('banner Off sends {enabled:false}', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'Off' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toEqual({ enabled: false });
});

test('banner Custom reveals platform+handle and sends the override', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'Custom' }));
  const twitchBtns = screen.getAllByRole('button', { name: 'Twitch' });
  fireEvent.click(twitchBtns[twitchBtns.length - 1]); // the banner drawer's platform picker
  fireEvent.change(screen.getByLabelText('Banner handle'), { target: { value: 'xqc' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].banner).toEqual({ platform: 'twitch', handle: 'xqc', y_pct: 0.85 });
});

test('catchup select value rides the start payload', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('button', { name: 'From now only' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].catchup).toBe('live_only');
});

test('catchup defaults to backfill', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].catchup).toBe('backfill');
});

test('single Zernio profile: picker stays hidden and "default" rides the start payload', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  await screen.findByLabelText('Channel');
  expect(screen.queryByText('Zernio profile')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].zernio_profile).toBe('default');
});

test('multiple Zernio profiles: picker appears and the selection rides the start payload', async () => {
  const { startLiveMonitor, getZernioProfiles } = await import('./realApi');
  getZernioProfiles.mockResolvedValueOnce({
    profiles: [
      { id: 'default', label: 'Default', configured: true },
      { id: 'ebay_live', label: 'eBay Live', configured: true },
    ],
  });
  render(<LiveMonitorView />);
  await screen.findByText('Zernio profile');
  fireEvent.click(screen.getByRole('button', { name: 'eBay Live' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].zernio_profile).toBe('ebay_live');
});

test('subtitle override section untouched → start payload has no compose key', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].compose).toBeUndefined();
});

// Before this, the Live Monitor start form had no hook-style controls at
// all — build_monitor_compose's hook was always plain text at position
// 'top', no background, regardless of what the same user configured (and
// saw working) on the Create tab. This pins that "Customize hook style"
// actually reaches the start payload.
test('hook style override section switched on → start payload carries compose.hook_params (style only, no text/position)', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize hook style' }));
  const bannerRow = screen.getByText('Banner behind text').closest('.edit-opt');
  fireEvent.click(within(bannerRow).getByRole('switch'));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const hookParams = startLiveMonitor.mock.calls[0][0].compose.hook_params;
  expect(hookParams.bg_enabled).toBe(true);
  expect(hookParams.text).toBeUndefined();
  expect(hookParams.position).toBeUndefined();
});

test('hook style + subtitle overrides both on → one compose object carries both', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize subtitles' }));
  fireEvent.click(screen.getByRole('switch', { name: 'Customize hook style' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const { compose } = startLiveMonitor.mock.calls[0][0];
  expect(compose.subtitle_params).toEqual(expect.objectContaining({ position: 'bottom' }));
  expect(compose.hook_params).toBeDefined();
});

test('subtitle override section switched on → start payload carries a compose.subtitle_params key', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize subtitles' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].compose).toEqual({ subtitle_params: expect.objectContaining({ position: 'bottom' }) });
});

test('classic-mode subtitle override translates keys in the start payload (bg_opacity/bg_color/border_color, no bg/outline_color)', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.click(screen.getByRole('switch', { name: 'Customize subtitles' }));
  fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
  const bgRow = screen.getByText('Background box').closest('.opt');
  fireEvent.click(within(bgRow).getByRole('switch'));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  const params = startLiveMonitor.mock.calls[0][0].compose.subtitle_params;
  expect(params.bg_opacity).toBe(0.6);
  expect(params.bg_color).toBe('#000000');
  expect(params.border_color).toBe('#000000');
  expect(params.bg).toBeUndefined();
  expect(params.outline_color).toBeUndefined();
});

test('classic-mode subtitle override translates keys in the config Applica payload too', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const overridesRow = screen.getByText('Subtitle overrides').closest('.opt');
  fireEvent.click(within(overridesRow).getByRole('switch'));
  fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
  const bgRow = screen.getByText('Background box').closest('.opt');
  fireEvent.click(within(bgRow).getByRole('switch'));
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  const params = updateMonitorConfig.mock.calls[0][1].compose.subtitle_params;
  expect(params.bg_opacity).toBe(0.6);
  expect(params.bg_color).toBe('#000000');
  expect(params.border_color).toBe('#000000');
  expect(params.bg).toBeUndefined();
  expect(params.outline_color).toBeUndefined();
});

test('Settings drawer hook style override → Apply payload carries compose.hook_params', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.click(screen.getByRole('switch', { name: 'Customize hook style kick:xqc' }));
  const bannerRow = screen.getByText('Banner behind text').closest('.edit-opt');
  fireEvent.click(within(bannerRow).getByRole('switch'));
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalled());
  const params = updateMonitorConfig.mock.calls[0][1].compose.hook_params;
  expect(params.bg_enabled).toBe(true);
});

test('Settings drawer prefills the hook style toggle+values from the running monitor status().config', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{
      id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: {
        compose: { hook_params: { bg_enabled: true, bg_color: '#00FF00' } },
      },
    }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  // Drawer already open in "banner on" state (proves the persisted hook_params seeded it).
  expect(screen.getByText('Banner color')).toBeInTheDocument();
  const bannerRow = screen.getByText('Banner behind text').closest('.edit-opt');
  expect(within(bannerRow).getByRole('switch')).toHaveAttribute('aria-checked', 'true');
});

test('Settings drawer prefills from the running monitor status().config', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{
      id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: {
        instructions: 'find hype moments', caption_template: '{hook}', title_template: '{title}',
        segment_seconds: 900, prelive_skip_seconds: 60, min_gap_seconds: 300,
        compose: {
          subtitle_params: {
            mode: 'classic', preset: 'classic_white', font: 'Montserrat-Black', font_color: '#FFFFFF',
            border_color: '#111111', border_width: 3, bg_opacity: 0.6, bg_color: '#000000',
            position: 'top', align: 'left', offset_y: 10,
          },
        },
      },
    }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByLabelText('Settings instructions kick:xqc')).toHaveValue('find hype moments');
  expect(screen.getByLabelText('Settings caption template kick:xqc')).toHaveValue('{hook}');
  expect(screen.getByLabelText('Settings title template kick:xqc')).toHaveValue('{title}');
  expect(screen.getByLabelText('Settings segment minutes kick:xqc')).toHaveValue(15);
  expect(screen.getByLabelText('Settings prelive skip minutes kick:xqc')).toHaveValue(1);
  expect(screen.getByLabelText('Settings min gap minutes kick:xqc')).toHaveValue(5);
  // Subtitle override drawer already open in classic mode with Background box on
  // (proves fromComposeSubtitleParams seeded mode/bg from the persisted config).
  expect(screen.getByText('Background box')).toBeInTheDocument();
  const bgRow = screen.getByText('Background box').closest('.opt');
  expect(within(bgRow).getByRole('switch')).toHaveAttribute('aria-checked', 'true');
});

test('publishing toggle calls setMonitorPublishing with the flipped value', async () => {
  const { setMonitorPublishing } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', publishing_enabled: false, pending_publish: 3 }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('Paused — 3 clip(s) waiting')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('switch', { name: 'Zernio auto-publish kick:xqc' }));
  await waitFor(() => expect(setMonitorPublishing).toHaveBeenCalledWith('kick:xqc', true));
});

test('config Applica posts only changed/allowed fields (instructions + caption_template)', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  fireEvent.change(screen.getByLabelText('Settings instructions kick:xqc'), { target: { value: 'find hype moments' } });
  fireEvent.change(screen.getByLabelText('Settings caption template kick:xqc'), { target: { value: '{hook}' } });
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', {
    instructions: 'find hype moments',
    caption_template: '{hook}',
  }));
});

test('shows Gemini rate-limit notice when gemini_exhausted_at set', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing',
      channel: 'xqc', gemini_exhausted_at: '2026-07-23T09:00:00Z' }],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText(/rate.?limit/i)).toBeInTheDocument();
});

test('no Gemini rate-limit notice when gemini_exhausted_at is unset', async () => {
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc' }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  expect(screen.queryByText(/rate.?limit/i)).toBeNull();
});

test('delete-after-publish checkbox seeds true by default and sends nothing when untouched', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc', config: {} }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const toggle = screen.getByRole('switch', { name: 'Delete after publish kick:xqc' });
  expect(toggle).toHaveAttribute('aria-checked', 'true');
  fireEvent.change(screen.getByLabelText('Settings instructions kick:xqc'), { target: { value: 'find hype moments' } });
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', { instructions: 'find hype moments' }));
});

test('delete-after-publish checkbox seeds false from config and sends only when toggled', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{ id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: { delete_after_publish: false } }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  const toggle = screen.getByRole('switch', { name: 'Delete after publish kick:xqc' });
  expect(toggle).toHaveAttribute('aria-checked', 'false');
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', { delete_after_publish: true }));
});

test('monitor card shows the custom name, falling back to channel when blank', async () => {
  mockStatus.mockResolvedValue({
    monitors: [
      { id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc', label: 'DJA — Kick' },
      { id: 'youtube:@MrBeast', platform: 'youtube', mode: 'vod', running: true, state: 'watching', channel: '@MrBeast' },
    ],
  });
  render(<LiveMonitorView />);
  expect(await screen.findByText('DJA — Kick')).toBeInTheDocument();
  // The unnamed monitor still shows its channel, with no stray label line.
  expect(screen.getByText('@MrBeast')).toBeInTheDocument();
});

test('monitor name rides the start payload', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Monitor name'), { target: { value: '  eBay Live — Kick  ' } });
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].label).toBe('eBay Live — Kick');
});

test('blank name still starts fine with an empty label', async () => {
  const { startLiveMonitor } = await import('./realApi');
  render(<LiveMonitorView />);
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(startLiveMonitor).toHaveBeenCalled());
  expect(startLiveMonitor.mock.calls[0][0].label).toBe('');
});

test('Settings drawer prefills and renames the monitor via the Name field', async () => {
  const { updateMonitorConfig } = await import('./realApi');
  mockStatus.mockResolvedValue({
    monitors: [{
      id: 'kick:xqc', platform: 'kick', mode: 'live', running: true, state: 'capturing', channel: 'xqc',
      config: { label: 'DJA — Kick' },
    }],
  });
  render(<LiveMonitorView />);
  await screen.findByText('xqc');
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByLabelText('Settings name kick:xqc')).toHaveValue('DJA — Kick');
  fireEvent.change(screen.getByLabelText('Settings name kick:xqc'), { target: { value: 'eBay Live — Kick' } });
  fireEvent.click(screen.getByRole('button', { name: /Apply/ }));
  await waitFor(() => expect(updateMonitorConfig).toHaveBeenCalledWith('kick:xqc', { label: 'eBay Live — Kick' }));
});

test('twitch missing-credentials (400) points to Settings', async () => {
  const { startLiveMonitor } = await import('./realApi');
  startLiveMonitor.mockRejectedValueOnce(new Error(
    'Twitch monitoring requires TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET (set them in Settings or the environment)'));
  const pushToast = vi.fn();
  render(<LiveMonitorView pushToast={pushToast} />);
  fireEvent.click(screen.getByRole('button', { name: 'Twitch' }));
  fireEvent.change(screen.getByLabelText('Channel'), { target: { value: 'xqc' } });
  await waitFor(() => expect(screen.getByRole('button', { name: /Start monitor/ })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: /Start monitor/ }));
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('error', expect.stringMatching(/Twitch not configured/i)));
});
