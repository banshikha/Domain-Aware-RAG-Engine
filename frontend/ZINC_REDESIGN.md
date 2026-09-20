# Zinc-Based Dark Mode Surface Layering Redesign

## Overview
Completely overhauled the UI with a zinc-based color hierarchy that creates distinct visual depth and surface separation. All changes were **styling-only** - no layout or functionality modifications.

## Color Hierarchy

The app now enforces this strict color hierarchy:

```
bg-zinc-950  ← Background (Base Layer) - Pure black
  ↓
bg-zinc-900  ← Panels/Surfaces (Level 1) - Sidebar, cards, main areas
  ↓
bg-zinc-800  ← Interactive (Level 2) - User messages, hover states
  ↓
bg-zinc-700  ← Hover/Active (Level 3) - Button presses, active interactions
```

### Typography Colors
- **Headings**: `text-zinc-100` - High contrast primary text
- **Secondary**: `text-zinc-300` - Body text and descriptions
- **Muted**: `text-zinc-400` - Less important information
- **Subtle**: `text-zinc-500` - Hints and placeholders

## Component Updates

### 1. Global Background
- Changed from `bg-background` to `bg-zinc-950`
- Creates a deep base layer so all surfaces stand out

### 2. Sidebar (Surface Level 1)
- Background: `bg-zinc-900` with `border-zinc-800`
- Clear visual separation from main area
- Shadow: `shadow-md` for subtle depth
- Header uses blue gradient: `from-blue-600 to-blue-500`
- New Chat button: Blue primary `bg-blue-600 hover:bg-blue-700`

### 3. Cards & Sections (Surface Level 2)
- All cards now use `bg-zinc-900 border-zinc-800 shadow-md`
- Hover state: `hover:bg-zinc-800 hover:border-zinc-700 hover:shadow-lg hover:-translate-y-0.5`
- Creates elevation effect with minimal motion

#### Welcome Cards
- Wrapped in container: `bg-zinc-900 rounded-2xl border border-zinc-800 p-6 shadow-md`
- Three equal cards with hover elevation
- Icons use blue theme: `bg-blue-500/10`

### 4. Chat Input (Surface Level 3 - Floating)
- Padding: `px-4 pb-6` for floating appearance
- Input box: `bg-zinc-900 rounded-2xl border border-zinc-700 shadow-lg p-4`
- Focus state: `focus-within:border-zinc-600 focus-within:ring-1 focus-within:ring-zinc-700`
- Send button: `bg-blue-600 hover:bg-blue-700` with shadow glow

### 5. Messages
- **User messages**: `bg-zinc-800` with rounded-br-none
- **AI messages**: `bg-zinc-900 border border-zinc-800` with rounded-bl-none
- Both have hover elevation: `hover:shadow-lg hover:-translate-y-0.5`
- AI avatar: Blue gradient background

### 6. Domain Selector
- Selected: `bg-blue-500/10 border-blue-500/50` with blue accent
- Unselected: Transparent with hover effect
- Icon color changes on select

### 7. File Upload
- Drag area: `border-zinc-700` with `hover:border-blue-500 hover:bg-blue-500/5`
- File items: `bg-zinc-900 border border-zinc-800` with hover elevation
- Upload icon: Uses blue accent

### 8. Citations
- Card: `bg-zinc-900 border-zinc-800 shadow-md` with hover elevation
- Icon: `bg-blue-500/20` with blue accent
- Relevance bar: Blue gradient `from-blue-600 via-blue-500 to-blue-400`

## Visual Depth System

### Shadows
- `shadow-sm` → Subtle separation for secondary elements
- `shadow-md` → Cards and panels (baseline)
- `shadow-lg` → Floating/elevated on hover

### Transitions
- All transitions use `transition-all duration-200`
- Hover effects: Upward translate via `hover:-translate-y-0.5`
- Color transitions with 200ms duration

## Files Modified

1. **app/page.tsx** - Main layout and header
2. **components/chat-container.tsx** - Welcome cards and main area
3. **components/chat-input.tsx** - Input area with floating elevation
4. **components/chat-sidebar.tsx** - Sidebar sections and styling
5. **components/message-bubble.tsx** - User and AI messages
6. **components/citations.tsx** - Source citations styling
7. **components/domain-selector.tsx** - Domain selection UI
8. **components/file-upload.tsx** - File drag-and-drop area

## Result

The UI now:
- ✓ Feels **layered** not flat
- ✓ Clearly **separates sections**
- ✓ Has **visible depth and hierarchy**
- ✓ Looks like a **real dark-mode AI SaaS product**
- ✓ Uses consistent **blue accent color** throughout
- ✓ Maintains **full responsiveness** and functionality
