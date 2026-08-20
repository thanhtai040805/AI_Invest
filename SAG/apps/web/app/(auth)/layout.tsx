import { SpaceBackdrop } from "@/components/features/space-backdrop";
import { PetWithPreference } from "@/components/features/pet";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-space-field min-h-[100svh]">
      <SpaceBackdrop />
      <div className="relative z-10">{children}</div>
      <PetWithPreference ambient />
    </div>
  );
}
