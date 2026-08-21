export interface CartLine {
  id: string;
  productId: string;
  quantity: number;
  name: string;
  slug: string;
  price: number;
  lotNumber: string;
}

export interface CheckoutInput {
  email: string;
  sessionId: string;
}

export interface CommandKpi {
  label: string;
  value: string;
  hint: string;
  empty: boolean;
}
