import { useState } from 'react';
import type { Platform } from './types';

export function usePlatform() {
  const [platform, setPlatformState] = useState<Platform>(() => {
    return (localStorage.getItem('fcpm_platform') as Platform) ?? 'pc';
  });

  function setPlatform(p: Platform) {
    localStorage.setItem('fcpm_platform', p);
    setPlatformState(p);
  }

  return { platform, setPlatform };
}
