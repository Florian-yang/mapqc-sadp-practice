#include <iostream>
#include <iomanip>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#endif
using namespace std;

class Dish {
public:
    virtual ~Dish() = default;
    virtual string description() const = 0;
    virtual int priceCents() const = 0;
};

class BaseNoodle : public Dish {
    string name_;
    int cents_;
public:
    BaseNoodle(const string& name, int cents) : name_(name), cents_(cents) {}
    string description() const override { return name_; }
    int priceCents() const override { return cents_; }
};

class Topping : public Dish {
protected:
    unique_ptr<Dish> inner_;
public:
    explicit Topping(unique_ptr<Dish> inner) : inner_(move(inner)) {}
};

class PricedTopping : public Topping {
    string name_;
    int cents_;
public:
    PricedTopping(unique_ptr<Dish> inner, const string& name, int cents)
        : Topping(move(inner)), name_(name), cents_(cents) {}
    string description() const override { return inner_->description() + " + " + name_; }
    int priceCents() const override { return inner_->priceCents() + cents_; }
};

struct MenuItem { string name; int cents; };
const vector<MenuItem> bases = {
    {"热干面", 600}, {"热干粉", 600}, {"汤面", 700}, {"汤粉", 700}
};
const vector<MenuItem> toppings = {
    {"时蔬", 150}, {"鸡肉", 400}, {"煎蛋", 200}, {"三鲜", 500}, {"牛肉", 600},
    {"牛肚", 600}, {"排骨", 600}, {"肥肠", 600}, {"鳝鱼", 700}, {"腰花", 600}
};

void showMenu(const vector<MenuItem>& menu) {
    for (size_t i = 0; i < menu.size(); ++i)
        cout << i + 1 << ' ' << menu[i].name << ' ' << menu[i].cents / 100.0 << "元\n";
}

int readIndex(const string& text, size_t count) {
    istringstream input(text);
    int id;
    char extra;
    if (!(input >> id) || (input >> extra) || id < 1 || id > static_cast<int>(count))
        throw invalid_argument("编号无效，请按菜单输入整数编号");
    return id - 1;
}

int main() {
#ifdef _WIN32
    SetConsoleOutputCP(CP_UTF8);
#endif
    cout << fixed << setprecision(2);
    cout << "遥记天天见面（演示单价，调味配菜计入面底）\n";
    string line;
    int result = 0;
    try {
        showMenu(bases);
        cout << "选择面底编号：";
        if (!getline(cin, line)) throw invalid_argument("未输入面底编号");
        const auto& base = bases[readIndex(line, bases.size())];
        unique_ptr<Dish> dish = make_unique<BaseNoodle>(base.name, base.cents);

        showMenu(toppings);
        cout << "输入浇头编号，空格分隔；重复表示多份，直接回车表示不加：";
        if (!getline(cin, line)) throw invalid_argument("未输入浇头，直接回车可表示不加");
        istringstream input(line);
        string token;
        int count = 0;
        while (input >> token) {
            if (++count > 100) throw invalid_argument("单碗浇头不能超过100份");
            const auto& topping = toppings[readIndex(token, toppings.size())];
            dish = make_unique<PricedTopping>(move(dish), topping.name, topping.cents);
        }
        cout << dish->description() << " = " << dish->priceCents() / 100.0 << " 元\n";
    } catch (const exception& e) {
        cout << e.what() << '\n';
        result = 1;
    }
    cout << "按回车退出。";
    getline(cin, line);
    return result;
}
