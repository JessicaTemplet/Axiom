mod nominal;

use axiom_ffi::host::AxRuntime;

fn main() {
    let rt = AxRuntime::claim().expect("the host owns the runtime");
    for n in 0..1_000 {
        let value = nominal::make_thing(rt, n);
        assert_eq!(nominal::read_thing(rt, &value), n);
        let wrapped = nominal::make_wrapped(rt, n).expect("Some local Thing");
        assert_eq!(nominal::read_thing(rt, &wrapped), n);
        match nominal::make_local(rt, n) {
            nominal::Result_Int_String::LocalOk(value) => assert_eq!(value, n),
            _ => panic!("local Result keeps its own constructors"),
        }
        assert_eq!(nominal::make_standard(rt, n).expect("standard Ok"), n);
        match nominal::make_local(rt, -1) {
            nominal::Result_Int_String::LocalError(text) => {
                assert_eq!(text.as_str().unwrap(), "local error");
            }
            _ => panic!("local error constructor"),
        }
        let text = nominal::make_standard(rt, -1).expect_err("standard Err");
        assert_eq!(text.as_str().unwrap(), "standard error");
    }
    println!("nominal host bindings agree");
}
