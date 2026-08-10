import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { LibraryPage } from '@/pages/LibraryPage'
import { SearchPage } from '@/pages/SearchPage'
import { FacesPage } from '@/pages/FacesPage'
import { EditStudioPage } from '@/pages/EditStudioPage'
import { TouchUpPage } from '@/pages/TouchUpPage'
import { SettingsPage } from '@/pages/SettingsPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<LibraryPage />} />
        <Route path="/search" element={<SearchPage />} />
        <Route path="/faces" element={<FacesPage />} />
        <Route path="/studio" element={<EditStudioPage />} />
        <Route path="/touchup" element={<TouchUpPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
