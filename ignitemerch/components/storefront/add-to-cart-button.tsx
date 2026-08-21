import { addToCart } from "@/app/actions/cart-actions";
import { cn } from "@/lib/utils";

interface AddToCartButtonProps {
  productId: string;
  label?: string;
  className?: string;
}

export function AddToCartButton({
  productId,
  label = "Add to ticket",
  className,
}: AddToCartButtonProps) {
  return (
    <form action={addToCart.bind(null, productId)}>
      <button
        type="submit"
        className={cn(
          "inline-flex h-12 items-center justify-center bg-ember px-5 font-mono text-[11px] uppercase tracking-[0.18em] text-soot transition-[transform,background-color] duration-200 ease-expo hover:bg-ember-hot active:scale-[0.96]",
          className,
        )}
      >
        {label}
      </button>
    </form>
  );
}
