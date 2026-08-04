// CreateView — pins the opts-key mapping of the recipe drawers (SubConfig /
// LogoConfig / grade row). These are the exact `set({...})` patches
// RedesignApp persists and later maps to backend keys, so the shared-controls
// refactor must keep emitting them byte-identically.
import { test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within, waitFor } from '@testing-library/react';
import { CreateView } from './create.jsx';
import { getZernioProfiles } from './realApi';

vi.mock('./realApi', () => ({
  listFonts: vi.fn(async () => ({ fonts: [] })),
  getZernioProfiles: vi.fn(async () => ({
    profiles: [{ id: 'default', label: 'Default', configured: false }],
  })),
}));

const BASE_OPTS = {
  mode: 'single', source: 'url', url: 'https://youtu.be/x', file: null,
  batch: '', batchFiles: [],
  preset: null, clipsAuto: true, clips: 3, aspect: '9:16',
  detect: false, model: '', reframeMode: 'auto', smartcut: false, zoom: false,
  language: 'multi',
  subtitles: true, subMode: 'karaoke',
  hooks: false, logo: true, gradePreset: 'none',
};

function mount(optsOver = {}, { onSaveAsDefault } = {}) {
  const set = vi.fn();
  render(<CreateView opts={{ ...BASE_OPTS, ...optsOver }} set={set}
    onPickPreset={vi.fn()} onCreate={vi.fn()} presets={[]} defaultId={null}
    onSetDefault={vi.fn()} onDelete={vi.fn()} onSaveCurrent={vi.fn()}
    onSaveAsDefault={onSaveAsDefault || vi.fn()} />);
  return set;
}

const openDrawer = (label) =>
  fireEvent.click(screen.getByRole('button', { name: `Configure ${label}` }));

beforeEach(() => vi.clearAllMocks());

test('subtitle drawer karaoke: mode switch, size slider and colors patch sub* keys', () => {
  const set = mount();
  openDrawer('Subtitles');
  fireEvent.click(screen.getByRole('button', { name: 'Classic' }));
  expect(set).toHaveBeenLastCalledWith({ subMode: 'classic' });
  fireEvent.change(screen.getByLabelText('Subtitle font size'), { target: { value: '42' } });
  expect(set).toHaveBeenLastCalledWith({ subFontSize: 42 });
  fireEvent.change(screen.getByLabelText('Subtitle text color'), { target: { value: '#ff0000' } });
  expect(set).toHaveBeenLastCalledWith({ subColor: '#ff0000' });
  fireEvent.change(screen.getByLabelText('Subtitle stroke color'), { target: { value: '#00ff00' } });
  expect(set).toHaveBeenLastCalledWith({ subStroke: '#00ff00' });
});

test('subtitle drawer classic: font/swatch/outline/bg patch their sub* keys', () => {
  const set = mount({ subMode: 'classic' });
  openDrawer('Subtitles');
  fireEvent.click(screen.getByLabelText('Font color #FDE700'));
  expect(set).toHaveBeenLastCalledWith({ subColor: '#FDE700' });
  fireEvent.change(screen.getByLabelText('Subtitle outline width'), { target: { value: '5' } });
  expect(set).toHaveBeenLastCalledWith({ subOutlineW: 5 });
  // Switches on screen: recipe rows (subtitles/smartcut/zoom/detect/hooks/logo)
  // + the drawer's Background box, which sits inside the drawer element.
  const drawer = document.querySelector('.cfg-drawer');
  fireEvent.click(drawer.querySelector('[role="switch"]'));
  expect(set).toHaveBeenLastCalledWith({ subBg: true });
});

test('subtitle drawer shared rows: position/alignment/nudge patch their sub* keys', () => {
  const set = mount();
  openDrawer('Subtitles');
  fireEvent.click(screen.getByRole('button', { name: 'Top' }));
  expect(set).toHaveBeenLastCalledWith({ subPosition: 'top' });
  fireEvent.click(screen.getByRole('button', { name: 'Left' }));
  expect(set).toHaveBeenLastCalledWith({ subAlign: 'left' });
  fireEvent.change(screen.getByLabelText('Subtitle vertical position'), { target: { value: '-12' } });
  expect(set).toHaveBeenLastCalledWith({ subOffsetY: -12 });
});

test('logo drawer: position cell and size segment patch logoPos/logoSize', () => {
  const set = mount();
  openDrawer('Brand logo');
  fireEvent.click(screen.getByRole('button', { name: 'Bot C' }));
  expect(set).toHaveBeenLastCalledWith({ logoPos: 'bottom-center' });
  fireEvent.click(screen.getByRole('button', { name: 'L' }));
  expect(set).toHaveBeenLastCalledWith({ logoSize: 'L' });
});

test('grade row: preset segments (with the extra Off entry) patch gradePreset', () => {
  const set = mount();
  // Scope to the grade row — the Reframe segmented also has an "Off" button.
  const row = within(screen.getByText('Colour grade').closest('.opt'));
  fireEvent.click(row.getByRole('button', { name: 'Warm' }));
  expect(set).toHaveBeenLastCalledWith({ gradePreset: 'warm_cinematic' });
  fireEvent.click(row.getByRole('button', { name: 'Off' }));
  expect(set).toHaveBeenLastCalledWith({ gradePreset: 'none' });
});

test('single Zernio profile: campaign picker stays hidden', async () => {
  mount();
  await waitFor(() => expect(getZernioProfiles).toHaveBeenCalled());
  expect(screen.queryByText('Campaign')).not.toBeInTheDocument();
});

test('multiple Zernio profiles: picker appears and selecting one patches zernioProfile', async () => {
  getZernioProfiles.mockResolvedValueOnce({
    profiles: [
      { id: 'default', label: 'Default', configured: true },
      { id: 'dja', label: 'DJA', configured: true },
    ],
  });
  const set = mount();
  await screen.findByText('Campaign');
  fireEvent.click(screen.getByRole('button', { name: 'DJA' }));
  expect(set).toHaveBeenLastCalledWith({ zernioProfile: 'dja' });
});

test('reframe picker: Gaming option patches reframeMode and shows its hint', () => {
  const set = mount({ reframeMode: 'gaming' });
  expect(screen.getByText(/Detects a static facecam overlay/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Subject' }));
  expect(set).toHaveBeenLastCalledWith({ reframeMode: 'subject' });
});

test('reframe picker: hint is hidden for non-gaming modes', () => {
  mount({ reframeMode: 'auto' });
  expect(screen.queryByText(/Detects a static facecam overlay/)).not.toBeInTheDocument();
});

test('stream layout editor: hidden outside gaming mode', () => {
  mount({ reframeMode: 'auto' });
  expect(screen.queryByText('Facecam position')).not.toBeInTheDocument();
  expect(screen.queryByText('Upload stream screenshot')).not.toBeInTheDocument();
});

test('stream layout editor: shown in gaming mode with both regions armable', () => {
  mount({ reframeMode: 'gaming' });
  expect(screen.getByText('Upload stream screenshot')).toBeInTheDocument();
  // Both source regions are drawable, and both preview zones are labelled.
  expect(screen.getAllByText('Facecam').length).toBeGreaterThan(0);
  expect(screen.getAllByText('Gameplay').length).toBeGreaterThan(0);
  // Nothing drawn yet → no clear buttons.
  expect(screen.queryByRole('button', { name: 'Reset facecam' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Reset gameplay' })).not.toBeInTheDocument();
});

test('stream layout editor: clear button removes only the region it names', () => {
  const set = mount({
    reframeMode: 'gaming',
    gamingFacecamBox: { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
    gamingGameplayBox: { x: 0.3, y: 0.3, w: 0.4, h: 0.4 },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Reset gameplay' }));
  expect(set).toHaveBeenLastCalledWith({ gamingGameplayBox: null });
});

test('stream layout editor: preview guides expose the three output heights', () => {
  mount({ reframeMode: 'gaming' });
  // The split/hook/subtitle positions live on the DELIVERED frame, so they are
  // draggable guides on the 9:16 preview rather than boxes on the screenshot.
  for (const label of ['Split', 'Text hook', 'Subtitles']) {
    expect(screen.getByRole('slider', { name: label })).toBeInTheDocument();
  }
});

test('stream layout editor: dragging the split guide reports a clamped fraction', () => {
  const set = mount({ reframeMode: 'gaming' });
  const split = screen.getByRole('slider', { name: 'Split' });
  // ArrowUp nudges by 1% — from the 0.45 default that is 0.44.
  fireEvent.keyDown(split, { key: 'ArrowUp' });
  expect(set).toHaveBeenLastCalledWith({ gamingSplit: expect.closeTo(0.44, 5) });
});

test('Save as default button in the recipe panel header calls onSaveAsDefault', () => {
  const onSaveAsDefault = vi.fn();
  mount({}, { onSaveAsDefault });
  fireEvent.click(screen.getByRole('button', { name: 'Save as default' }));
  expect(onSaveAsDefault).toHaveBeenCalledTimes(1);
});
