// SettingsView key-status badges — pins the bug fix where the "set"/"empty"
// badge must reflect backend-confirmed `present` state, never the raw input
// text, and must refresh after save/clear instead of going stale or being
// wiped by a transient getConfig failure.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { SettingsView, HistoryView } from './views.jsx';

const getConfig = vi.fn();
const saveConfig = vi.fn();

vi.mock('./realApi', () => ({
  getConfig: (...a) => getConfig(...a),
  saveConfig: (...a) => saveConfig(...a),
  getModels: vi.fn(async () => ({ models: [] })),
  cookiesStatus: vi.fn(async () => ({ configured: false })),
  uploadCookies: vi.fn(),
  deleteCookies: vi.fn(),
  getZernio: vi.fn(async () => ({ configured: false })),
  saveZernio: vi.fn(),
  discoverZernioAccounts: vi.fn(),
  getZernioProfiles: vi.fn(async () => ({
    profiles: [{ id: 'default', label: 'Default', configured: false }],
  })),
  createZernioProfile: vi.fn(),
  listFonts: vi.fn(async () => ({ fonts: [] })),
  uploadFont: vi.fn(),
  deleteFont: vi.fn(),
  logoStatus: vi.fn(async () => ({ configured: false })),
  uploadLogo: vi.fn(),
  deleteLogo: vi.fn(),
  listPlayerImages: vi.fn(async () => ({ players: [] })),
  uploadPlayerImage: vi.fn(),
  deletePlayerImage: vi.fn(),
}));

const EMPTY_CONFIG = { GEMINI_API_KEY: '', HF_TOKEN: '', DEEPGRAM_API_KEY: '', ELEVENLABS_API_KEY: '' };
const SET_CONFIG = { ...EMPTY_CONFIG, GEMINI_API_KEY: 'AIza...xyz1' };

beforeEach(() => {
  vi.clearAllMocks();
  saveConfig.mockResolvedValue({ success: true });
});

function mount(pushToast = vi.fn()) {
  render(<SettingsView pushToast={pushToast} />);
  return pushToast;
}

const geminiRow = () => screen.getByLabelText('Gemini').closest('.keyrow');

test('badge reflects backend state, not input text typed before any save', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  // Typing into the field (no blur/save yet) must not flip the badge.
  fireEvent.change(screen.getByLabelText('Gemini'), { target: { value: 'AIzaSomeKey' } });
  expect(within(geminiRow()).getByText('empty')).toBeInTheDocument();
  expect(within(geminiRow()).queryByText('set')).toBeNull();
});

test('save triggers a refetch and the badge updates from the backend response', async () => {
  getConfig.mockResolvedValueOnce(EMPTY_CONFIG).mockResolvedValueOnce(SET_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());

  const input = screen.getByLabelText('Gemini');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'AIzaSomeKey' } });
  fireEvent.blur(input);

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: 'AIzaSomeKey' });
  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());
});

test('a failed post-save refetch does not wipe previously-known present state', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(null);
  const pushToast = mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  const input = screen.getByLabelText('Deepgram');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'dg_key' } });
  fireEvent.blur(input);

  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(2));
  // Gemini's badge (unrelated to the Deepgram save) must still read "set".
  expect(within(geminiRow()).getByText('set')).toBeInTheDocument();
  expect(pushToast).toHaveBeenCalledWith('warn', expect.any(String));
});

test('clearing a present key saves an empty value and the badge flips to empty', async () => {
  getConfig.mockResolvedValueOnce(SET_CONFIG).mockResolvedValueOnce(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(within(geminiRow()).getByText('set')).toBeInTheDocument());

  fireEvent.click(within(geminiRow()).getByRole('button', { name: 'Clear Gemini key' }));

  expect(saveConfig).toHaveBeenCalledWith({ GEMINI_API_KEY: '' });
  await waitFor(() => expect(within(geminiRow()).getByText('empty')).toBeInTheDocument());
});

test('Twitch client id/secret rows reflect backend present state', async () => {
  getConfig.mockResolvedValue({ ...EMPTY_CONFIG, TWITCH_CLIENT_ID: 'abcd1234', TWITCH_CLIENT_SECRET: 'shhh12345678' });
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  const secretRow = () => screen.getByLabelText('Twitch client secret').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('set')).toBeInTheDocument());
  expect(within(secretRow()).getByText('set')).toBeInTheDocument();
});

test('Twitch client id/secret rows show empty when unset', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  const idRow = () => screen.getByLabelText('Twitch client ID').closest('.keyrow');
  await waitFor(() => expect(within(idRow()).getByText('empty')).toBeInTheDocument());
});

// --- Zernio profiles: picker visibility + create flow -----------------------

test('single Zernio profile: picker hidden, add-profile button present', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add Zernio profile' })).toBeInTheDocument());
  expect(screen.queryByRole('button', { name: 'Default' })).not.toBeInTheDocument();
});

test('multiple Zernio profiles: picker appears and switching profile refetches getZernio for it', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  const { getZernio, getZernioProfiles } = await import('./realApi');
  getZernioProfiles.mockResolvedValueOnce({
    profiles: [
      { id: 'default', label: 'Default', configured: true },
      { id: 'ebay_live', label: 'eBay Live', configured: false },
    ],
  });
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: 'eBay Live' })).toBeInTheDocument());
  getZernio.mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'eBay Live' }));
  await waitFor(() => expect(getZernio).toHaveBeenCalledWith('ebay_live'));
});

test('creating a new Zernio profile switches the active profile to it', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  const { createZernioProfile, getZernio } = await import('./realApi');
  createZernioProfile.mockResolvedValue({
    profiles: [
      { id: 'default', label: 'Default', configured: false },
      { id: 'ebay_live', label: 'eBay Live', configured: false },
    ],
  });
  mount();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add Zernio profile' })).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Add Zernio profile' }));
  fireEvent.change(screen.getByPlaceholderText('profile id (e.g. ebay_live)'), { target: { value: 'ebay_live' } });
  fireEvent.change(screen.getByPlaceholderText('Label (optional)'), { target: { value: 'eBay Live' } });
  fireEvent.click(screen.getByRole('button', { name: 'Create' }));
  await waitFor(() => expect(createZernioProfile).toHaveBeenCalledWith('ebay_live', 'eBay Live'));
  await waitFor(() => expect(getZernio).toHaveBeenCalledWith('ebay_live'));
});

// --- Player image library -----------------------------------------------

test('player images list is empty by default', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  mount();
  await waitFor(() => expect(screen.getByPlaceholderText('Player name (e.g. LeBron James)')).toBeInTheDocument());
  expect(screen.queryByText('LeBron James')).not.toBeInTheDocument();
});

test('uploading a player image without a name shows a warning toast and does not upload', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  const { uploadPlayerImage } = await import('./realApi');
  const pushToast = mount();
  await waitFor(() => expect(screen.getByPlaceholderText('Player name (e.g. LeBron James)')).toBeInTheDocument());
  const file = new File(['x'], 'photo.png', { type: 'image/png' });
  const input = screen.getByPlaceholderText('Player name (e.g. LeBron James)').closest('.opt').querySelector('input[type="file"]');
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(pushToast).toHaveBeenCalledWith('warn', 'Enter a player name first'));
  expect(uploadPlayerImage).not.toHaveBeenCalled();
});

test('uploading a player image with a name calls uploadPlayerImage and refreshes the list', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  const { uploadPlayerImage } = await import('./realApi');
  uploadPlayerImage.mockResolvedValue({ name: 'LeBron James', players: ['LeBron James'] });
  mount();
  const nameInput = await screen.findByPlaceholderText('Player name (e.g. LeBron James)');
  fireEvent.change(nameInput, { target: { value: 'LeBron James' } });
  const file = new File(['x'], 'photo.png', { type: 'image/png' });
  const fileInput = nameInput.closest('.opt').querySelector('input[type="file"]');
  fireEvent.change(fileInput, { target: { files: [file] } });
  await waitFor(() => expect(uploadPlayerImage).toHaveBeenCalledWith('LeBron James', file));
  await waitFor(() => expect(screen.getByText('LeBron James')).toBeInTheDocument());
});

test('deleting a player image calls deletePlayerImage and removes it from the list', async () => {
  getConfig.mockResolvedValue(EMPTY_CONFIG);
  const { listPlayerImages, deletePlayerImage } = await import('./realApi');
  listPlayerImages.mockResolvedValueOnce({ players: ['LeBron James'] });
  deletePlayerImage.mockResolvedValue({ players: [] });
  mount();
  await waitFor(() => expect(screen.getByText('LeBron James')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Remove LeBron James' }));
  await waitFor(() => expect(deletePlayerImage).toHaveBeenCalledWith('LeBron James'));
  await waitFor(() => expect(screen.queryByText('LeBron James')).not.toBeInTheDocument());
});

// HistoryView — title + per-job "published" badge (derived from
// history_service.scan_history's additive `title`/`publishedCount` fields).
test('history row shows the video title and a published-count badge when clips were published', () => {
  const history = [
    { jobId: 'job-1', status: 'complete', clipCount: 2, source: 'my video', title: 'my video', publishedCount: 1, timestamp: Date.now() },
    { jobId: 'job-2', status: 'complete', clipCount: 3, source: 'other video', title: 'other video', publishedCount: 0, timestamp: Date.now() },
  ];
  render(<HistoryView history={history} availableIds={null} onOpen={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} />);

  expect(screen.getByText('my video')).toBeInTheDocument();
  expect(screen.getByText('1 pubblicate')).toBeInTheDocument();
  expect(screen.getByText('other video')).toBeInTheDocument();
  expect(screen.queryByText('0 pubblicate')).toBeNull();
});

test('a stale config read cannot revert a newer setting change', async () => {
  // Two quick changes → two save/refresh sequences that interleave. The FIRST
  // read must not land after the SECOND save and drag the control back to the
  // value it had before, which is what made settings appear not to stick.
  let releaseStaleRead;
  const staleRead = new Promise((resolve) => { releaseStaleRead = resolve; });

  getConfig
    // Mount read.
    .mockResolvedValueOnce({ ...EMPTY_CONFIG, CLIPPYME_MAX_DOWNLOAD_HEIGHT: '1080' })
    // Refresh after change #1 — held open, resolves LAST with a stale payload.
    .mockImplementationOnce(() => staleRead)
    // Refresh after change #2 — resolves first, carrying the newest value.
    .mockResolvedValueOnce({ ...EMPTY_CONFIG, CLIPPYME_MAX_DOWNLOAD_HEIGHT: '0' });

  mount();
  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(1));

  fireEvent.click(screen.getByRole('button', { name: '1440p' }));
  await waitFor(() => expect(saveConfig).toHaveBeenCalledWith({ CLIPPYME_MAX_DOWNLOAD_HEIGHT: '1440' }));

  fireEvent.click(screen.getByRole('button', { name: 'Best' }));
  await waitFor(() => expect(saveConfig).toHaveBeenCalledWith({ CLIPPYME_MAX_DOWNLOAD_HEIGHT: '0' }));
  await waitFor(() => expect(getConfig).toHaveBeenCalledTimes(3));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Best' })).toHaveAttribute('aria-pressed', 'true'));

  // Now let the stale first read resolve — it must be discarded, not applied.
  releaseStaleRead({ ...EMPTY_CONFIG, CLIPPYME_MAX_DOWNLOAD_HEIGHT: '1080' });
  await staleRead;

  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Best' })).toHaveAttribute('aria-pressed', 'true'));
  expect(screen.getByRole('button', { name: '1080p' })).toHaveAttribute('aria-pressed', 'false');
});
