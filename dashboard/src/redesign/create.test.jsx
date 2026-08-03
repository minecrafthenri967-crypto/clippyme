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

test('gaming facecam controls: hidden outside gaming mode', () => {
  mount({ reframeMode: 'auto' });
  expect(screen.queryByText('Facecam position')).not.toBeInTheDocument();
});

test('gaming facecam controls: position grid defaults to Auto and size row is hidden', () => {
  mount({ reframeMode: 'gaming' });
  const positionRow = screen.getByText('Facecam position').closest('.cf-row');
  expect(within(positionRow).getByRole('button', { name: 'Auto' })).toHaveClass('on');
  expect(screen.queryByText('Facecam size')).not.toBeInTheDocument();
});

test('gaming facecam controls: picking a corner patches gamingFacecamPosition and reveals size row', () => {
  const set = mount({ reframeMode: 'gaming' });
  const positionRow = screen.getByText('Facecam position').closest('.cf-row');
  fireEvent.click(within(positionRow).getByRole('button', { name: 'Top L' }));
  expect(set).toHaveBeenLastCalledWith({ gamingFacecamPosition: 'top-left' });
});

test('gaming facecam controls: size segment patches gamingFacecamSize once a manual position is set', () => {
  const set = mount({ reframeMode: 'gaming', gamingFacecamPosition: 'bottom-right' });
  const sizeRow = screen.getByText('Facecam size').closest('.cf-row');
  fireEvent.click(within(sizeRow).getByRole('button', { name: 'L' }));
  expect(set).toHaveBeenLastCalledWith({ gamingFacecamSize: 'L' });
});

test('Save as default button in the recipe panel header calls onSaveAsDefault', () => {
  const onSaveAsDefault = vi.fn();
  mount({}, { onSaveAsDefault });
  fireEvent.click(screen.getByRole('button', { name: 'Save as default' }));
  expect(onSaveAsDefault).toHaveBeenCalledTimes(1);
});
