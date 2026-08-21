import { getCart } from "@/app/actions/cart-actions";
import { NavBar } from "@/components/storefront/nav-bar";

export async function Navigation() {
  const cart = await getCart();
  return <NavBar cartCount={cart.count} />;
}
