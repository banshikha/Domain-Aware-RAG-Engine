# Visual Hierarchy & Depth Enhancement

## Overview
The UI has been completely redesigned to create a premium SaaS aesthetic with clear visual layering, improved depth perception, and enhanced interaction feedback.

---

## 1. Visual Hierarchy Improvements

### Background Layering
- **Layer 0 (Base)**: Pure black (`0 0% 0%`) - Creates strong foundation
- **Layer 1 (Sidebar)**: Very dark (`0 0% 3%`) - Subtle elevation from background
- **Layer 2 (Cards)**: Dark gray (`0 0% 10%`) - Clear distinction from background
- **Layer 3 (Accents)**: Medium gray (`0 0% 18%`) - Secondary interactive elements

### Border System
- Primary borders: `0 0% 18%` with 40-70% opacity - Subtle but visible
- Secondary borders: `0 0% 15%` with reduced opacity - Understated separation
- Hover states: Borders become more opaque for depth cues

---

## 2. Card Design Enhancements

### Base Card Styling
All cards now use the `.card-elevated` class with:
- Rounded corners (`rounded-lg` / `rounded-xl`)
- Soft shadows (`shadow-md` / `shadow-lg`) for depth
- Subtle borders with opacity control
- Background color distinction from main surface

### Hover Effects
Cards implement `.card-hover` with:
- Elevated shadows (`shadow-lg` / `shadow-xl`)
- Border color shift toward primary (`primary/50`)
- Subtle upward translation (`translate-y-[-2px]`)
- Smooth transitions (`smooth-transition` class)

### Card Types

#### Welcome Cards
```
- Card elevation: shadow-md
- Hover elevation: shadow-lg
- Icon background: primary/10 → primary/15
- Spacing: Centered layout with 5px padding
```

#### Citation Cards
```
- Icon: 10x10px with primary/12 background
- Relevance bar: Gradient from primary to primary/50
- Source badge: Medium gray background
- Hover effect: Icon background deepens, border lifts
```

#### Domain/Upload Section Cards
```
- Background: sidebar-accent/30
- Border: sidebar-border/60
- Padding: 4px (p-4)
- Hover: Enhanced shadow and border opacity
```

---

## 3. Input Area Elevation

### Floating Input Design
The chat input area now has:
- **Gradient background**: `from-background via-background/95 to-background/70`
- **Elevated shadow**: `shadow-2xl shadow-black/30` for floating effect
- **Backdrop blur**: `backdrop-blur-xl` for modern glassmorphism
- **Padding increase**: `p-6` for more breathing room

### Input Field Enhancement
- Class: `.input-elevated`
- Gradient: `from-input to-input/80`
- Focus state: `ring-2 ring-primary/40`
- Shadow: `shadow-lg shadow-black/20`
- Rounded corners: `rounded-xl` for premium feel

### Send Button
- Gradient: `from-primary to-primary/90`
- Shadow: `shadow-lg shadow-primary/30`
- Hover shadow: `shadow-primary/40` with slight translation
- Border radius: `rounded-lg` for cohesion

---

## 4. Section Separation

### Sidebar Structure
Three distinct sections with visual separation:

**Domain Section**
- Title: Bold uppercase with tracking
- Container: `rounded-xl bg-sidebar-accent/30 p-4 border border-sidebar-border/60`
- Shadow: `shadow-sm` with hover elevation

**Documents Section**
- Same styling as Domain Section
- Added spacing (mt-5)

**Quick Start Section**
- Title: Same typography as above
- Container: Gradient background `from-sidebar-accent/25 to-sidebar-accent/10`
- Items: Numbered badges with primary color
- Border: `sidebar-border/50`

---

## 5. Color System

### Dark Mode (Default)
- **Background**: Pure black for maximum contrast
- **Cards**: 10% gray for subtle elevation
- **Borders**: 18% gray with variable opacity
- **Sidebar**: 3% gray (darker than background)
- **Primary**: Cyan blue (`200 100% 50%`) for CTAs

### Light Mode
- **Background**: Pure white
- **Cards**: 95% gray
- **Borders**: 85% gray
- **Sidebar**: 97% gray
- **Primary**: Cyan blue (maintains saturation)

### Color Hierarchy
1. **Deepest**: Background (0%)
2. **Deep**: Sidebar (3%)
3. **Medium**: Card backgrounds (10%)
4. **Light**: Accents (18%)
5. **Brightest**: Primary color elements

---

## 6. Depth & Layering System

### Shadow Hierarchy
- **No shadow**: Base text, icons
- **shadow-sm**: Subtle section containers
- **shadow-md**: Main card elements, elevation start
- **shadow-lg**: Elevated cards on hover, input area
- **shadow-xl**: Floating elements, strong hover states

### Shadow Colors
- Dark backgrounds: `shadow-black/5` to `shadow-black/30`
- Primary elements: `shadow-primary/10` to `shadow-primary/40`
- Subtle gradation creates dimensional effect

### Transform States
- Idle: `translate-y-0`
- Hover: `translate-y-[-2px]` for cards
- Active buttons: `translate-y-[-1px]` for subtle feedback

---

## 7. Interaction Feedback

### Smooth Transitions
All interactive elements use `.smooth-transition`:
- Duration: `300ms`
- Easing: `ease-out`
- Properties: `all`
- Covers: color, shadow, transform, border

### Button States
```
- Default: Base shadow and color
- Hover: Elevated shadow, translate up, color shift
- Active: Reduced shadow, return to base position
- Disabled: Reduced opacity, no transform
```

### Border States
```
- Default: border-border/40 to border-border/60
- Hover: Increases opacity to border-border/80
- Focused: border-primary/50 for input elements
```

---

## 8. Typography Hierarchy

### Section Headers
- Font size: `text-xs`
- Font weight: `font-bold`
- Letter spacing: `tracking-widest`
- Opacity: `opacity-80`
- Case: `uppercase`

### Card Titles
- Font size: `text-sm`
- Font weight: `font-semibold` / `font-bold`
- Color: `text-foreground` or `text-primary` on hover

### Body Text
- Font size: `text-xs` / `text-sm`
- Line height: `leading-relaxed` for readability
- Color: `text-muted-foreground` for secondary text

---

## 9. Spacing System

### Padding Standards
- Small containers: `p-3` / `p-4`
- Medium containers: `p-4` / `p-5`
- Large containers: `p-6`
- Input area: `p-6` for breathing room

### Gap Standards
- Tight spacing: `gap-2`
- Standard spacing: `gap-3` / `gap-4`
- Loose spacing: `gap-5`

### Margin Standards
- Vertical spacing between sections: `space-y-4` / `space-y-5`
- Horizontal spacing: Minimal in compact areas
- Message spacing: `mb-6` for breathing room

---

## 10. Animation & Motion

### Entrance Animations
- Messages: `animate-in fade-in slide-in-from-bottom-4` with staggered delays
- Citations: `animate-in fade-in slide-in-from-top-2` with offset delay
- Delay progression: `${index * 100}ms`

### Hover Animations
- Smooth color transitions: `300ms ease-out`
- Transform effects: `translate-y-[-2px]` with same duration
- Shadow elevation: Simultaneous with transform
- Icon animations: Color changes follow button hover

### Loading States
- Bounce animation: Three dots with `animation-delay` progression
- Delay sequence: `0ms`, `150ms`, `300ms`
- Creates wave effect for visual interest

---

## 11. Component-Specific Enhancements

### Message Bubbles
- **User messages**: Gradient `from-primary to-primary/90` with strong shadow
- **AI messages**: Card style with border elevation
- **Hover effect**: Cards lift and glow with primary shadow
- **Timestamps**: Reduced opacity for hierarchy

### Welcome State
- Large icon in gradient container with primary glow
- Centered multi-line title with balanced spacing
- Three equally-spaced feature cards in grid layout
- Emoji icons in elevated containers
- Descriptions in muted foreground for secondary hierarchy

### Sidebar
- Header with gradient icon and bold typography
- Section labels with consistent styling
- Content containers with subtle elevation
- Quick start items with numbered badges
- Footer buttons with standard elevation

---

## 12. Premium SaaS Visual Cues

✓ **Depth**: Multiple shadow layers create dimensional effect
✓ **Contrast**: Clear color separation between UI layers
✓ **Polish**: Smooth transitions and micro-interactions
✓ **Consistency**: Unified design language across all components
✓ **Hierarchy**: Typography and spacing guide visual priority
✓ **Affordance**: Hover states clearly indicate interactivity
✓ **Breathing Room**: Generous spacing reduces visual clutter
✓ **Modern Aesthetic**: Gradient accents and glassmorphism effects
✓ **Accessibility**: Sufficient color contrast throughout
✓ **Responsiveness**: Adapts gracefully to mobile and desktop

---

## Technical Implementation

### CSS Classes Introduced
- `.card-elevated` - Base card styling with shadow
- `.card-hover` - Hover effects with transform and shadow
- `.card-floating` - Floating container for input area
- `.button-elevated` - Button elevation with transform
- `.smooth-transition` - Unified transition properties
- `.section-container` - Background section styling
- `.panel-surface` - Secondary panel styling
- `.input-elevated` - Input field premium styling

### Color Variable Adjustments
- Background: `0 0% 0%` (pure black)
- Card: `0 0% 10%` (higher contrast)
- Border: `0 0% 18%` (more visible)
- Sidebar: `0 0% 3%` (distinct layer)
- Input: `0 0% 9%` (slightly elevated)

### Responsive Considerations
- Mobile header with theme toggle
- Hamburger menu for sidebar
- Touch-friendly button sizes
- Stacked layout for smaller screens
- Full-screen optimizations

---

## Browser Compatibility

All enhancements use standard CSS features:
- CSS Grid & Flexbox
- CSS Variables
- Backdrop-filter (modern browsers)
- Transition & Transform
- Box-shadow with multiple layers
- Gradient backgrounds
- Rounded corners

Tested and optimized for:
- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)
- Mobile browsers

---

## Performance Considerations

- Minimal repaints: Use transform for animations (GPU accelerated)
- Transition durations: 200-300ms for smooth but responsive feel
- Shadow layers: Uses CSS variables for efficient rendering
- Backdrop blur: Hardware accelerated in modern browsers
- Animations: Staggered to avoid simultaneous rendering

---

## Future Enhancements

Potential improvements to consider:
1. Dark theme system toggle with smooth transition
2. Custom theme customization in settings
3. Additional accent color options
4. Animation preferences respecting `prefers-reduced-motion`
5. High contrast mode for accessibility
6. Custom shadow/elevation profiles per theme

---

Generated: 2026-04-19
Tailwind CSS v4 + Next.js 16
